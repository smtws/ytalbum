"""Lyrics for a track: LRCLIB lookup, the `.lrc` sidecar, the tag copy (DESIGN.md §7).

Only this module talks to lrclib.net. Hits are cached for 30 days, misses for 7.

The sidecar next to the audio is what ytalbum knows. `tag_file` replaces every tag on every
pass, so the `LYRICS` comment is written *from* the sidecar instead of being kept — a lyric
can then never be half-lost, and players that read tags see the same text as players that
read `.lrc` files (MPD/Volumio has no lyrics tag at all).

A match is refused unless a candidate's length is within `TOLERANCE` of the file's: a cover
or a live version is exactly what a title-only match would attach.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Protocol

import httpx

from . import user_agent
from .models import AlbumPlan, PlanTrack, Provenance
from .tag import audio_length, tagged_lyrics
from .text import key as text_key

log = logging.getLogger(__name__)

# `YTALBUM_LRCLIB_BASE=http://127.0.0.1:8794/api` points this at another host: a mirror, or — which
# is what it exists for — a server that speaks lrclib's documented shapes, so that publishing can be
# exercised end to end without putting test words into a public database (§9, slice 42).
BASE = (os.environ.get("YTALBUM_LRCLIB_BASE") or "https://lrclib.net/api").rstrip("/")
USER_AGENT = user_agent()
HIT_TTL = 30 * 24 * 3600
MISS_TTL = 7 * 24 * 3600
TOLERANCE = 3.0  # seconds a candidate's length may differ from ours (YouTube pads, we trim)
MAX_EXACT = 3600.0  # lrclib's /api/get answers "duration: must be between 1 and 3600"
SUFFIX = ".lrc"
TIMESTAMPED = re.compile(r"^\s*\[\d{1,3}:\d{2}", re.M)
# a title that says the recording has no singing: its words are the sung version's, not its
NO_VOCALS = re.compile(r"\b(instrumentals?|karaoke|backing track)\b", re.I)
# the same markers, with the bracket group around them, for the *query* (see `query_title`)
MARKER = re.compile(r"[(\[]\s*(?:instrumental\s+version|instrumentals?|karaoke|backing\s+track)\s*[)\]]"
                    r"|\s-\s*(?:instrumental\s+version|instrumentals?|karaoke|backing\s+track)\s*$"
                    r"|\b(?:instrumental\s+version|instrumentals?|karaoke|backing\s+track)\b", re.I)

# statuses kept in PlanTrack.lyrics
SYNCED, PLAIN, INSTRUMENTAL, NONE = "synced", "plain", "instrumental", "none"


def query_title(title: str) -> str:
    """The title to ask lrclib for: without an "(Instrumental)" marker, and without anything else.

    lrclib indexes recordings people submitted words for, so an instrumental cut has no entry of
    its own and a title carrying the marker matches nothing at all — the track then had no length
    reference either (J8), although the sung recording's length is exactly the reference it wants.
    Only these markers go. A "(Live)" or any other bracket group is sent as it stands, because the
    live cut really is a different recording and studio words must never attach to it.
    """
    cleaned = re.sub(r"\s{2,}", " ", MARKER.sub(" ", title)).strip(" -–—·|/,;")
    return cleaned or title


def consensus_length(durations: list[float]) -> float | None:
    """What the candidates agree the song is: the commonest whole second, the median on a tie.

    The nearest candidate is the wrong answer for a padded upload, because what is nearest to a
    471 s video is whatever other padded copy exists (248 s for S1) — while the question the chip
    asks is how long the song is (about 230 s). A tie is settled by the median of the tied values,
    so the answer is never one that repeats less often than another.
    """
    if not durations:
        return None
    counts = Counter(round(d) for d in durations)
    most = max(counts.values())
    return float(median(sorted(seconds for seconds, n in counts.items() if n == most)))


class LyricsError(Exception):
    pass


PUBLISH_TIMEOUT = 60.0  # their end does real work on a publish; this is not a lookup
# Their live target is `000000FF000…` (asked on 2026-09-27, and the same one their docs print),
# so a solution needs three zero bytes: about 16.7 million tries on average, and the tail is long.
# 200 million is a minute or two of this machine and makes giving up mean something is wrong.
SOLVE_LIMIT = 200_000_000


def solve_challenge(prefix: str, target: str, limit: int = SOLVE_LIMIT) -> str:
    """The nonce that makes `prefix + nonce` hash low enough (their proof of work).

    Their own client (LRCGET's `challenge_solver.rs`) counts up from zero and compares the SHA-256
    digest with the target byte by byte: above it at any byte fails, below it at any byte succeeds,
    equal all the way through succeeds. This is the same walk, and it is pure so that a test can
    hand it a target of `ff…` and get `0` back without a network in sight.
    """
    goal = bytes.fromhex(target)
    head = prefix.encode()
    digest = hashlib.sha256
    # `digest <= goal` **is** their byte-by-byte rule: comparing two byte strings of the same length
    # in Python is lexicographic and big-endian, which is what "above it at any byte fails, below it
    # at any byte succeeds" means. Written as a loop it was four times slower, and this runs about
    # seventeen million times for one publish.
    for nonce in range(limit):
        if digest(head + b"%d" % nonce).digest() <= goal:
            return str(nonce)
    raise LyricsError(f"could not solve lrclib's challenge in {limit} tries")


def _not_above(digest: bytes, target: bytes) -> bool:
    """The rule as their client spells it out, kept for the test that proves the fast form equals it."""
    for mine, theirs in zip(digest, target, strict=False):
        if mine > theirs:
            return False
        if mine < theirs:
            return True
    return True


def _publish_error(response: Any) -> str:
    """Their own words about the refusal, mapped to something a person can act on."""
    try:
        body = response.json()
        said = str(body.get("message") or body.get("name") or "")
    except Exception:
        said = (response.text or "")[:200].strip()
    if response.status_code == 400:
        return f"lrclib refused the words: {said or 'the publish token was not accepted'}"
    if response.status_code in (429, 503):
        return "lrclib is busy or rate-limiting; nothing was published — try again in a few minutes"
    return f"lrclib refused the words (HTTP {response.status_code}){': ' + said if said else ''}"


@dataclass
class Lyrics:
    synced: str | None = None  # LRC, with timestamps
    plain: str | None = None
    lrclib_id: int | None = None
    instrumental: bool = False
    length: float | None = None  # seconds of the recording this came from — a second opinion
                                 # on how long the song is, kept even when it was refused

    @property
    def text(self) -> str | None:
        return self.synced or self.plain

    @property
    def status(self) -> str:
        if self.instrumental:
            return INSTRUMENTAL
        return SYNCED if self.synced else PLAIN if self.plain else NONE


class LyricsAPI(Protocol):
    def get(self, artist: str, title: str, album: str | None, length: float | None,
            skip: Collection[int] = ()) -> Lyrics | None: ...
    def by_id(self, lrclib_id: int) -> Lyrics | None: ...


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "ytalbum" / "lyrics.sqlite3"


class Lrclib:
    def __init__(
        self,
        cache_path: Path | None = None,
        client: httpx.Client | None = None,
        min_interval: float = 0.5,
        retries: int = 3,
    ) -> None:
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=20,
            follow_redirects=True,
        )
        self.min_interval = min_interval
        self.retries = retries
        self._lock = threading.Lock()
        self._last = 0.0
        self._db: sqlite3.Connection | None = None
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(cache_path, check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, body TEXT, expires REAL)")

    # -- public API ----------------------------------------------------------------------

    def get(self, artist: str, title: str, album: str | None = None, length: float | None = None,
            skip: Collection[int] = ()) -> Lyrics | None:
        """The lyrics of this recording, or None when nothing matches it closely enough.

        Without a length nothing is accepted: the length is the only thing that tells a
        recording apart from its covers. `skip` holds entries the user has rejected for this
        track: they are dropped before anything is judged, words and length alike, because an
        entry that is not this song is no evidence about how long this song is either (§9, slice 27).
        """
        if length is None:
            return None
        silent = bool(NO_VOCALS.search(title))  # "(instrumental)": the sung words are not its
        asked = query_title(title)  # the marker is not part of any entry's name
        exact = None
        if album and length <= MAX_EXACT:
            # the exact endpoint wants lrclib's own album name and ±2s (measured 2026-09-25),
            # so it answers for real albums and never for our compilation names
            params = {"artist_name": artist, "track_name": asked, "duration": str(round(length))}
            if (found := self._request("get", {**params, "album_name": album})) and found.get("id") not in skip:
                exact = _pick([found], length, silent)
                if exact and exact.text:
                    return exact
        # An entry without words is not an answer yet: lrclib's "instrumental" is set when
        # nobody has submitted lyrics, not only when a recording has none — 16 of the 18
        # entries for "Blöde Frage, Saufgelage" are such stubs, and one of them beat the
        # synced entry of the very same length because the exact endpoint answered first.
        rows = self._request("search", {"artist_name": artist, "track_name": asked}) or []
        same = [
            row
            for row in rows
            if isinstance(row.get("duration"), int | float)
            and row.get("id") not in skip
            and _same_artist(artist, row.get("artistName") or "")
        ]
        fits = [row for row in same if abs(row["duration"] - length) <= TOLERANCE]
        if picked := _pick(fits, length, silent):
            return picked
        if exact:
            return exact  # the album's own entry, and nobody has words for this song
        # Nothing close enough to be this recording — but how long lrclib thinks the song is, is
        # worth knowing: it is the second opinion on a file that carries an intro. That is a
        # question about the song, so every same-artist candidate answers it together.
        agreed = consensus_length([row["duration"] for row in same])
        return Lyrics(length=agreed) if agreed is not None else None

    def candidates(self, artist: str, title: str) -> list[Lyrics]:
        """Every same-artist entry with words for this title, however far its length is (§9, slice 46).

        `get` answers "is there a match"; this answers "what is there at all", which is the question
        the near-miss check asks before deciding with an alignment rather than with a length.
        """
        rows = self._request("search", {"artist_name": artist, "track_name": query_title(title)}) or []
        return [_lyrics(row) for row in rows
                if isinstance(row.get("duration"), int | float)
                and _same_artist(artist, row.get("artistName") or "")
                and (row.get("syncedLyrics") or row.get("plainLyrics"))]

    def by_id(self, lrclib_id: int) -> Lyrics | None:
        """One known entry, for deciding whether a sidecar is still the one we wrote.

        Cached search bodies already carry whole rows, so a track looked up recently costs
        nothing; only an older one reaches the network.
        """
        if row := self._cached_row(lrclib_id):
            return _lyrics(row)
        found = self._request(f"get/{lrclib_id}", {})
        return _lyrics(found) if isinstance(found, dict) else None

    def cached_by_id(self, lrclib_id: int) -> Lyrics | None:
        """What we already hold for an entry, **without asking** (§9, slice 42).

        The publish button needs to know whether lrclib's words and the user's are the same text,
        and that question is asked for every track of an album as a panel opens. A lookup each time
        would be a lot of traffic for a button nobody has pressed, so an unknown entry simply means
        "cannot tell", and the answer is then the user's own in the confirm.
        """
        row = self._cached_row(lrclib_id)
        return _lyrics(row) if row else None

    def _cached_row(self, lrclib_id: int) -> dict[str, Any] | None:
        if self._db is None:
            return None
        for (body,) in self._db.execute("SELECT body FROM cache WHERE key LIKE 'search%'"):
            rows = json.loads(body)
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("id") == lrclib_id:
                        return row
        return None

    # -- transport -----------------------------------------------------------------------

    def _request(self, path: str, params: dict[str, str]) -> Any:
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        if (cached := self._cache_get(key)) is not None:
            return cached or None

        for attempt in range(self.retries + 1):
            self._wait_turn()
            try:
                r = self.client.get(f"{BASE}/{path}", params=params)
            except httpx.HTTPError as e:
                if attempt == self.retries:
                    raise LyricsError(f"{path}: {e}") from e
                time.sleep(2**attempt)
                continue
            if r.status_code in (429, 503):  # lrclib answers "server is busy" fairly often
                if attempt == self.retries:
                    raise LyricsError(f"{path}: HTTP {r.status_code} after {attempt + 1} tries")
                time.sleep(2**attempt)
                continue
            if 400 <= r.status_code < 500:
                # "TrackNotFound", or a request lrclib will never accept (a track over an hour):
                # a real answer either way, and asking again every run would only annoy it
                log.debug("%s: HTTP %s %s", path, r.status_code, r.text[:120])
                self._cache_put(key, None, MISS_TTL)
                return None
            if r.status_code >= 500:
                raise LyricsError(f"{path}: HTTP {r.status_code}")
            body = r.json()
            self._cache_put(key, body, MISS_TTL if not body else HIT_TTL)
            return body
        return None

    # -- giving words back (§9, slice 42) -------------------------------------------------------

    def publish(self, *, track_name: str, artist_name: str, album_name: str, duration: float,
                plain: str, synced: str, log_to: Callable[[str], None] | None = None) -> None:
        """Publish one set of words to LRCLIB, anonymously (their `POST /api/publish`).

        The flow is their proof-of-work one, documented on 2026-09-27 at <https://lrclib.net/docs>:
        ask `POST /api/request-challenge` for a `prefix` and a `target`, find a `nonce` whose
        SHA-256 of `prefix + nonce` does not exceed the target, and send the two as
        `X-Publish-Token: prefix:nonce`. No account, no key, and each token works once.

        **The publish request is never retried.** Asking for a challenge again is free — it changes
        nothing — but a second POST could be a second copy of the same words in a public database,
        which is not a thing anybody can take back. A failure is reported and that is the end of it.
        """
        say = log_to or (lambda _: None)
        challenge = self._challenge()
        say(f"solving lrclib's challenge (target {challenge['target'][:8]}…)")
        started = time.monotonic()
        nonce = solve_challenge(challenge["prefix"], challenge["target"])
        say(f"solved in {time.monotonic() - started:.1f} s")
        body = {"trackName": track_name, "artistName": artist_name, "albumName": album_name,
                "duration": round(float(duration), 2), "plainLyrics": plain, "syncedLyrics": synced}
        self._wait_turn()
        try:
            r = self.client.post(f"{BASE}/publish", json=body,
                                 headers={"X-Publish-Token": f"{challenge['prefix']}:{nonce}"},
                                 timeout=PUBLISH_TIMEOUT)
        except httpx.HTTPError as e:
            # it may or may not have arrived; either way this code will not send it twice
            raise LyricsError(f"lrclib could not be reached: {e}") from e
        if r.status_code in (200, 201):
            return
        raise LyricsError(_publish_error(r))

    def _challenge(self) -> dict[str, str]:
        """A fresh prefix and target. Retried, because asking costs nothing and changes nothing."""
        for attempt in range(self.retries + 1):
            self._wait_turn()
            try:
                r = self.client.post(f"{BASE}/request-challenge", timeout=20)
            except httpx.HTTPError as e:
                if attempt == self.retries:
                    raise LyricsError(f"lrclib could not be reached: {e}") from e
                time.sleep(2**attempt)
                continue
            if r.status_code in (429, 503) and attempt < self.retries:
                time.sleep(2**attempt)
                continue
            if r.status_code not in (200, 201):
                raise LyricsError(f"lrclib would not set a challenge (HTTP {r.status_code})")
            got = r.json()
            if not (got.get("prefix") and got.get("target")):
                raise LyricsError("lrclib's challenge was missing its prefix or target")
            return {"prefix": str(got["prefix"]), "target": str(got["target"])}
        raise LyricsError("lrclib would not set a challenge")

    def _wait_turn(self) -> None:
        with self._lock:
            delay = self._last + self.min_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()

    def _cache_get(self, key: str) -> Any:
        if not self._db:
            return None
        row = self._db.execute("SELECT body, expires FROM cache WHERE key = ?", (key,)).fetchone()
        if row and row[1] > time.time():
            return json.loads(row[0]) or {}  # {} = "nothing there", told apart from a cache miss
        return None

    def _cache_put(self, key: str, body: Any, ttl: float) -> None:
        if self._db:
            with self._db:
                self._db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, json.dumps(body), time.time() + ttl))


def _has_words(row: dict[str, Any]) -> bool:
    return bool(row.get("syncedLyrics") or row.get("plainLyrics"))


def _pick(fits: list[dict[str, Any]], length: float, silent: bool) -> Lyrics | None:
    """The best candidate of this length: words first, unless the track says it has none."""
    if silent:
        # an instrumental cut is as long as the sung one, so length cannot tell them apart;
        # the title can, and borrowing the singer's words would be plainly wrong
        quiet = [row for row in fits if not _has_words(row)]
        return _lyrics(min(quiet, key=lambda row: abs(row["duration"] - length))) if quiet else None
    if worded := [row for row in fits if _has_words(row)]:
        return _lyrics(min(worded, key=lambda row: (not row.get("syncedLyrics"), abs(row["duration"] - length))))
    return _lyrics(min(fits, key=lambda row: abs(row["duration"] - length))) if fits else None


def _lyrics(row: dict[str, Any]) -> Lyrics | None:
    found = Lyrics(
        synced=row.get("syncedLyrics") or None,
        plain=row.get("plainLyrics") or None,
        lrclib_id=row.get("id"),
        instrumental=bool(row.get("instrumental")),
        length=row.get("duration"),
    )
    return found if found.text or found.instrumental else None


def _same_artist(ours: str, theirs: str) -> bool:
    """lrclib's search is loose; a cover by somebody else must not pass as our recording."""
    a, b = text_key(ours), text_key(theirs)
    return bool(a) and bool(b) and (a in b or b in a)


# -- the sidecar file --------------------------------------------------------------------


def sidecar_path(album_dir: Path, filename: str) -> Path:
    """`01 Artist - Title.lrc` beside `01 Artist - Title.opus` — where players look for it."""
    return album_dir / (Path(filename).stem + SUFFIX)


def read_sidecar(album_dir: Path, track: PlanTrack) -> str | None:
    try:
        return sidecar_path(album_dir, track.filename).read_text(encoding="utf-8").strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def sidecar_sha(album_dir: Path, track: PlanTrack) -> str | None:
    """The fingerprint of the sidecar on disk, or None when there is none."""
    try:
        return hashlib.sha1(sidecar_path(album_dir, track.filename).read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def sidecar_lost(album_dir: Path, track: PlanTrack) -> bool:
    """The status claims words, but the file beside the track is gone — a pass must catch up."""
    return track.lyrics in (SYNCED, PLAIN) and not sidecar_path(album_dir, track.filename).exists()


def user_owns(album_dir: Path, track: PlanTrack) -> bool:
    """Whether these are the user's own words — only for as long as the file holding them is."""
    return track.provenance.get("lyrics") == Provenance.USER and sidecar_path(album_dir, track.filename).exists()


def write_sidecar(album_dir: Path, track: PlanTrack, text: str, length: float | None = None) -> Path:
    """Write the words and remember the bytes: anything else there later is the user's.

    Also remembers *which audio* these words were written against — the video they were timed to
    and how long that file was — so a later change of either can say so instead of leaving
    timestamps that quietly point at the wrong seconds (§9, slice 34).
    """
    path = sidecar_path(album_dir, track.filename)
    path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    track.lyrics_sha = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
    track.lyrics_for_source = track.effective_id
    track.lyrics_for_length = track.file_length if length is None else length
    return path


STALE_BY = 1.0  # seconds: less than this is the same recording measured twice, not another file


def timings_stale(track: PlanTrack) -> dict[str, Any] | None:
    """Were the sidecar's timestamps written for a different file than the one on disk?

    Only timestamps can be wrong in this way, so plain words never raise it. A sidecar from before
    this was recorded says nothing either — we do not know what it was written for, and guessing
    would put a notice on every old track (§9, slice 34).
    """
    if track.lyrics != SYNCED or not track.lyrics_for_source:
        return None
    was, now = track.lyrics_for_length, track.file_length
    moved = was is not None and now is not None and abs(now - was) > STALE_BY
    if track.lyrics_for_source == track.effective_id and not moved:
        return None
    return {"source": track.lyrics_for_source, "was": was, "now": now}


def remove_sidecar(album_dir: Path, filename: str) -> None:
    sidecar_path(album_dir, filename).unlink(missing_ok=True)


def rename_sidecar(album_dir: Path, old: str, new: str) -> None:
    """Follow the audio file: a renamed track keeps its lyrics."""
    was, now = sidecar_path(album_dir, old), sidecar_path(album_dir, new)
    if was != now and was.exists() and not now.exists():
        was.rename(now)


# -- who wrote the sidecar ----------------------------------------------------------------


def reconcile(album_dir: Path, track: PlanTrack, audio: Path) -> tuple[str | None, bool]:
    """Bring the plan in line with the sidecar on disk. Returns (text for the tag, changed).

    The record of what we wrote (`lyrics_sha`, the twin of `cover_fetched.sha1`) decides
    ownership. Sidecars from before that record are judged by the tag we last wrote from them:
    a sidecar that differs from the tag was edited since the last pass and is the user's. That
    catches recent edits only — an older edit was already written into the tag by a later pass —
    so the case a `--refetch` would destroy is caught in `update_track` instead, by asking
    lrclib what the entry we stored actually says (DESIGN.md §9, slice 21).
    """
    text = read_sidecar(album_dir, track)
    if text is None:
        changed = False
        if track.provenance.get("lyrics") == Provenance.USER:
            # The file the mark protected is gone, so there is nothing of the user's left to
            # protect — and keeping the mark would make "delete your file and --refetch" (the
            # way back to lrclib's version, README) skip the track for ever.
            del track.provenance["lyrics"]
            changed = True
        if track.lyrics in (SYNCED, PLAIN):  # the words are not on the disk any more
            track.lyrics, track.lyrics_sha = NONE, None
            changed = True
        return None, changed
    if track.provenance.get("lyrics") == Provenance.USER:
        return text, False
    if track.lyrics_sha is None:  # written before we kept a record
        if not track.lyrics_id:  # we never write a sidecar without noting where it came from
            track.provenance["lyrics"] = Provenance.USER
            log.info("%s: the lyrics beside this track are not ours — keeping them", track.title)
            return text, True
        tagged = tagged_lyrics(audio)
        if tagged is not None and tagged.strip() != text:
            track.provenance["lyrics"] = Provenance.USER
            log.info("%s: the lyrics beside this track were edited — they are yours now", track.title)
            return text, True
        # Equal to the tag proves nothing: every pass writes the tag *from* the sidecar, so an
        # edit made before the last pass reads back as agreement. Left undecided on purpose —
        # `update_track` asks lrclib about the stored entry if it ever wants to replace the file.
        return text, False
    if sidecar_sha(album_dir, track) != track.lyrics_sha:
        track.provenance["lyrics"] = Provenance.USER
        log.info("%s: the lyrics beside this track were edited — they are yours now", track.title)
        return text, True
    return text, False


# -- one track ---------------------------------------------------------------------------


STAMPS = re.compile(r"^\s*(?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*)+")


def plain_text(synced: str) -> str:
    """The same words with their timestamps taken off: lrclib stores both forms of an entry.

    A line that was nothing but a stamp — the `[03:05.66]` that marks where the singing stops —
    becomes an empty line, which is what it always was: a gap, not a word.
    """
    lines = [STAMPS.sub("", line).strip() for line in (synced or "").splitlines()]
    return "\n".join(lines).strip()


def sent_sha(text: str) -> str:
    """The fingerprint of what was published: the same shape as `lyrics_sha`, and never the words."""
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


# -- an entry that is nearly this recording (§9, slice 46) -------------------------------------------
#
# `get` accepts a candidate within TOLERANCE and nothing else, and it is right to: the length is all
# it has to tell a recording from its cover. But a measurement over this library's 203 near-misses
# found that the length gap says much less than it looks. 71% of the entries *beyond* 3% of the
# file's length were still this recording's words (`docs/qa-catalog.md`, section AG). What settles
# it is an alignment, which answers two questions at once: whether these are the song's words (how
# many lines it can place) and whether the entry's timings belong to *this* cut (how much of the
# singing they span).

NOMINATE_SHARE = 0.25   # beyond a quarter of the file's length, no alignment is spent on a candidate
FIT_SPAN = 0.85         # of the singing: a lyric that covers this much of it belongs to this cut
FIT_WIDE = 1.15         # and one wider than the singing does not — an 83 s file, a 222 s lyric
FIT_UNPLACED = 0.10     # lines the aligner could not place, where the words really are the song's
NOFIT_UNPLACED = 0.25   # and past this, they are not this song at all


def nominated(ours: float, theirs: float) -> bool:
    """Is this candidate worth an alignment? Cheap arithmetic before an expensive test."""
    return bool(ours) and abs(ours - theirs) <= NOMINATE_SHARE * ours


def fit_verdict(span: float | None, unplaced: float) -> str:
    """What an alignment of a candidate's words against our file says to do with it.

    `words+stamps` — take the entry whole, as a within-tolerance match. `words` — the words are this
    song's but the timings are another cut's, so keep the words and our own stamps. `reject` — the
    aligner could not find these words in this audio, so it is not this song. `unclear` — the
    instrument does not know, and nobody pretends otherwise: the panel shows the numbers and a person
    decides.
    """
    if unplaced > NOFIT_UNPLACED:
        return "reject"
    if span is None:
        return "unclear"
    if span > FIT_WIDE:
        return "words"          # the lyric is wider than the singing: right words, wrong recording
    if span >= FIT_SPAN and unplaced <= FIT_UNPLACED:
        return "words+stamps"
    if span < 0.75:
        return "words"
    return "unclear"


# The two verdicts that hand the question back: the words exist, nothing was taken, and only a
# person can settle it (§9, slice 46). Everything else is decided — taken, rejected, or never looked at.
WAITING = ("unclear", "shown")


def needs_you(track: Any) -> bool:
    """Whether this track is waiting for a person to decide about an lrclib entry."""
    fit = getattr(track, "lyrics_fit", None) or {}
    return fit.get("decided") in WAITING and (getattr(track, "lyrics", None) or "none") == "none"


def fit_reason(ours: float, theirs: float) -> str:
    """Why an entry's timings do not transfer, in the two shapes the measurement found."""
    return "a clip" if ours < theirs * (1 - NOMINATE_SHARE) else "another cut"


def publishable(track: PlanTrack, text: str, theirs: str | None = None) -> str:
    """Empty when these words may be given to lrclib; otherwise the reason they may not (§9, slice 42).

    The rules are all one idea: **only offer what is the user's own work and would be new to them.**
    A publish cannot be taken back, so every doubt resolves to "no".
    """
    if track.state != "done":
        return "there is no file beside these words yet"
    if (track.lyrics or "") == "instrumental":
        return "this track is marked instrumental"
    if status_of(text) != "synced":
        return "only timed lyrics are worth giving back — these have no timestamps"
    if track.provenance.get("lyrics") != Provenance.USER:
        return "these are lrclib's own words, not yours"
    if track.lyrics_words_by:
        return f"these words are a draft by {track.lyrics_words_by} — write them yourself first"
    if theirs is not None and text.strip() == theirs.strip():
        return "lrclib already has exactly these words"
    if (track.lyrics_published or {}).get("sha") == sent_sha(text):
        return "already published"
    return ""


def status_of(text: str) -> str:
    """Which kind of lyric this text is: timestamped or not. The only judge of that status."""
    return SYNCED if TIMESTAMPED.search(text) else PLAIN


def _ask_by_id(api: LyricsAPI, lrclib_id: int) -> Lyrics | None:
    try:
        return api.by_id(lrclib_id)
    except LyricsError as e:
        log.debug("lrclib entry %s could not be read: %s", lrclib_id, e)
        return None


def update_track(api: LyricsAPI, plan: AlbumPlan, track: PlanTrack, album_dir: Path, audio: Path) -> str | None:
    """Look the lyrics up once and write the sidecar. Returns the text to put in the tag.

    Sets `track.lyrics` so no track is looked up twice; a missing lyric is never an error
    (same rule as the cover: it must not stop an album).
    """
    if track.provenance.get("lyrics") == Provenance.USER:
        text = read_sidecar(album_dir, track)
        if text and track.lyrics is None:  # a trim cleared the status; the words are still yours
            track.lyrics = status_of(text)
        return text
    if (existing := read_sidecar(album_dir, track)) and track.lyrics_sha is None and track.lyrics_id:
        # about to replace a sidecar we have no record of. Ask lrclib what the entry we stored
        # holds: the same words mean it is ours, different words mean the user edited it, and
        # no answer at all means we keep it and ask again another day.
        stored = _ask_by_id(api, track.lyrics_id)
        if stored is None:
            track.lyrics = status_of(existing)  # whoever wrote them, these words are here
            log.info("%s: cannot check whose lyrics these are — keeping them", track.title)
            return existing
        if (stored.text or "").strip() != existing:
            track.provenance["lyrics"] = Provenance.USER
            track.lyrics = status_of(existing)  # the status describes the words on disk, now yours
            log.info("%s: the lyrics beside this track differ from the entry we saved — they are yours", track.title)
            return existing
        track.lyrics_sha = sidecar_sha(album_dir, track)  # ours after all; record it and carry on
    length = audio_length(audio)
    try:
        found = api.get(track.artist, track.title, plan.album, length, skip=track.lyrics_rejected)
    except LyricsError as e:
        log.debug("no lyrics for %s - %s: %s", track.artist, track.title, e)
        return read_sidecar(album_dir, track)  # ask again next time
    status = found.status if found else NONE
    if status == NONE and NO_VOCALS.search(track.title):
        # "none" means we looked and lrclib has nothing; here we know *why* there are no words
        status = INSTRUMENTAL
    track.lyrics = status
    track.lyrics_id = found.lrclib_id if found else None
    track.lyrics_length = found.length if found else None
    if not found or not found.text:
        remove_sidecar(album_dir, track.filename)
        track.lyrics_sha = None
        return None
    # no shifting: the match was gated on *this* file's length, so the timestamps of the
    # recording that matched are the timestamps of the file in front of us. A trim changes
    # that length, which is why a trim change makes the track be looked up again (download.py)
    text = found.text
    write_sidecar(album_dir, track, text, length)
    return text


__all__ = ["Lrclib", "Lyrics", "LyricsAPI", "LyricsError", "consensus_length", "query_title", "read_sidecar", "reconcile",
           "sidecar_lost", "status_of", "timings_stale", "update_track", "user_owns"]
