"""Pipeline stage 4b: MusicBrainz enrichment (DESIGN.md §4, slice 5).

MusicBrainz is an enricher, never a gatekeeper: nothing is dropped when it has no match.
- album level (official albums, artist playlists): accept a release only if title and
  artist match, the track count is within ±1 and ≥80% of the tracks match by title;
  then album/artist/year/tracklist/cover come from it.
- track level (everything not covered by a release): a recording must match title AND
  artist; if nothing matches, the swapped query catches "Song - Artist" titles.
Values set here become the plan's auto values, so user edits still win on merge.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from typing import Any

from .mb import MusicBrainzAPI, MusicBrainzError
from .models import AlbumPlan, Kind, PlanTrack, Provenance
from .plan import refresh_derived
from .titles import key

log = logging.getLogger(__name__)

# worldwide/European editions first (canonical tracklists), then big markets
COUNTRY_ORDER = ["XW", "XE", "DE", "US", "GB", "SE", "CA", "AU", "FR", "NL", "NO", "FI", "JP"]
MIN_TRACK_MATCH = 0.8
RELEASE_LOOKUPS = 3  # candidates to open per album (each is one request)

_GROUP = re.compile(r"\s*[(\[]([^()\[\]]*)[)\]]")
_FEAT = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\s", re.I)


# -- title helpers -------------------------------------------------------------------------


def core(title: str) -> str:
    """The comparable part of a title: no bracket groups, no trailing 'feat. X'."""
    title = _GROUP.sub("", title)
    title = _FEAT.split(title)[0]
    return title.strip(" -–—")


def feat_text(title: str) -> str:
    """Everything that names featured artists, e.g. '(feat. @xxFEUERSCHWANZxx)' -> 'xxfeuerschwanzxx'."""
    found = [g for g in _GROUP.findall(title) if _FEAT.search(g + " ")]
    tail = _FEAT.split(_GROUP.sub("", title), maxsplit=1)
    if len(tail) == 2:
        found.append(tail[1])
    return key(" ".join(found))


def kept_suffixes(ours: str, theirs: str, albums: list[str] = ()) -> str:
    """Bracket groups of our title that MusicBrainz' title lacks (e.g. '(Live)').

    Not kept: feat. credits (they go into the artist) and groups naming a release the
    recording appeared on ('[MASKENHAFT-Ein Versinken in elf Bildern]') - album info.
    """
    album_keys = [key(a) for a in albums if a]
    kept = []
    for m in _GROUP.finditer(ours):
        g, k = m[0].strip(), key(m[1])
        if _FEAT.search(m[1] + " ") or not k or k in key(theirs):
            continue
        if any(k in a or a in k for a in album_keys):
            continue
        kept.append(g)
    return "".join(f" {g}" for g in kept)


def credit_phrase(ac: list[dict[str, Any]]) -> str:
    return "".join(a.get("name", "") + a.get("joinphrase", "") for a in ac).strip()


def credit_names(ac: list[dict[str, Any]]) -> list[str]:
    return [a.get("name") or a.get("artist", {}).get("name", "") for a in ac]


def artist_matches(ours: str, ac: list[dict[str, Any]]) -> bool:
    """Our artist is the whole credit, or its main artist (as credited or canonical name)."""
    if not ac or not (k := key(ours)):
        return False
    main = ac[0]
    return k in {key(credit_phrase(ac)), key(main.get("name", "")), key(main.get("artist", {}).get("name", ""))}


# -- track level ---------------------------------------------------------------------------


def pick_recording(artist: str, title: str, recordings: list[dict[str, Any]]) -> dict[str, Any] | None:
    want, feat = key(core(title)), feat_text(title)
    ok = [r for r in recordings if key(core(r.get("title", ""))) == want and artist_matches(artist, r.get("artist-credit", []))]
    if not ok:
        return None

    def rank(r: dict[str, Any]) -> tuple:
        others = [key(n) for n in credit_names(r["artist-credit"])[1:]]
        feat_hit = bool(feat) and any(n and n in feat for n in others)
        unexpected_guests = bool(others) and not feat
        official_album = any(
            rel.get("status") == "Official" and (rel.get("release-group") or {}).get("primary-type") == "Album"
            for rel in r.get("releases", [])
        )
        return (not feat_hit, unexpected_guests, -int(r.get("score", 0)), not official_album)

    return min(ok, key=rank)


def enrich_track(t: PlanTrack, mb: MusicBrainzAPI) -> bool:
    rec = pick_recording(t.artist, t.title, mb.search_recordings(t.artist, core(t.title) or t.title))
    title_source = t.title
    if rec is None:  # maybe "Song - Artist": swap
        swapped_artist, swapped_title = core(t.title), t.artist
        if swapped_artist and swapped_title:
            rec = pick_recording(swapped_artist, swapped_title, mb.search_recordings(swapped_artist, swapped_title))
            title_source = swapped_title
    if rec is None:
        return False

    ac = rec["artist-credit"]
    extra = kept_suffixes(title_source, rec["title"], [r.get("title", "") for r in rec.get("releases", [])])
    title = rec["title"] + extra
    if feat_text(title_source) and len(ac) == 1:  # MB has no guest credit: keep ours
        title += "".join(f" {g}" for g in re.findall(r"\([^)]*feat[^)]*\)", title_source, re.I))
    _set(t, "artist", credit_phrase(ac))
    if length := rec.get("length"):
        t.mb_length = round(length / 1000, 1)  # lets the UI suggest where the song ends
    if extra:
        # "(Live)", "(Behind The Scenes Documentary)": the artist is confirmed, but this is
        # not that recording - keep our title, attach no recording id
        t.title = t.auto["title"] = title
        t.provenance["title"] = Provenance.YT_TITLE
    else:
        _set(t, "title", title)
        t.mbid = rec["id"]
    return True


# -- album level ---------------------------------------------------------------------------


def _country_rank(country: str | None) -> int:
    return COUNTRY_ORDER.index(country) if country in COUNTRY_ORDER else len(COUNTRY_ORDER)


def release_candidates(plan: AlbumPlan, releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    n = len(plan.tracks)
    ok = [
        r
        for r in releases
        if key(core(r.get("title", ""))) == key(core(plan.album))
        and artist_matches(plan.albumartist, r.get("artist-credit", []))
        and abs(int(r.get("track-count") or 0) - n) <= 1
    ]
    return sorted(
        ok,
        key=lambda r: (
            int(r.get("track-count") or 0) != n,
            r.get("status") != "Official",
            _country_rank(r.get("country")),
            r.get("date") or "9999",
            -int(r.get("score", 0)),
        ),
    )


def match_release_tracks(plan: AlbumPlan, release: dict[str, Any]) -> dict[int, dict[str, Any]] | None:
    """plan track index -> MB track (with 'disc' added), or None if too few tracks match."""
    mb_tracks = [{**t, "disc": m.get("position", 1)} for m in release.get("media", []) for t in m.get("tracks", [])]
    unused = list(range(len(mb_tracks)))
    matches: dict[int, dict[str, Any]] = {}
    for i, t in enumerate(plan.tracks):
        want = key(core(t.title))
        for j in unused:
            if key(core(mb_tracks[j]["title"])) == want:
                matches[i] = mb_tracks[j]
                unused.remove(j)
                break
    enough = len(matches) >= math.ceil(MIN_TRACK_MATCH * len(mb_tracks)) and len(matches) >= math.ceil(MIN_TRACK_MATCH * len(plan.tracks))
    return matches if mb_tracks and enough else None


def enrich_release(plan: AlbumPlan, mb: MusicBrainzAPI) -> bool:
    for cand in release_candidates(plan, mb.search_releases(plan.albumartist, core(plan.album)))[:RELEASE_LOOKUPS]:
        release = mb.release(cand["id"])
        if not release or (matches := match_release_tracks(plan, release)) is None:
            continue
        rg = release.get("release-group") or cand.get("release-group") or {}
        _set(plan, "album", release["title"])
        # the album belongs to the main artist; guest credits stay on the tracks
        _set(plan, "albumartist", credit_names(release["artist-credit"])[0] or credit_phrase(release["artist-credit"]))
        if year := (rg.get("first-release-date") or release.get("date") or "")[:4]:
            _set(plan, "year", int(year))
        plan.mbid = release["id"]
        if rg.get("id"):
            plan.cover_fallback_url = plan.cover_fallback_url or plan.cover_url
            plan.cover_url = f"https://coverartarchive.org/release-group/{rg['id']}/front-500"

        next_number = max((int(m["position"]) for m in matches.values()), default=0) + 1
        for i, t in enumerate(plan.tracks):
            if m := matches.get(i):
                _set(t, "title", m["title"])
                _set(t, "artist", credit_phrase(m.get("artist-credit") or release["artist-credit"]))
                t.number, t.disc, t.mbid = int(m["position"]), int(m["disc"]), m["recording"]["id"]
                if length := m.get("length") or m["recording"].get("length"):
                    t.mb_length = round(int(length) / 1000, 1)
            else:
                t.number, next_number = next_number, next_number + 1
        plan.tracks.sort(key=lambda t: (t.disc, t.number))
        return True
    return False


# -- entry point -----------------------------------------------------------------------------


def enrich(plan: AlbumPlan, mb: MusicBrainzAPI, progress: Callable[[str], None] = lambda s: None) -> dict[str, int]:
    """Enrich a fresh plan in place. Network errors degrade to 'no match', never abort."""
    stats = {"release": 0, "tracks": 0, "looked_up": 0}
    try:
        if plan.kind in (Kind.OFFICIAL_ALBUM, Kind.ARTIST_PLAYLIST):
            progress(f"MusicBrainz: looking for the release “{plan.album}”")
            stats["release"] = int(enrich_release(plan, mb))

        todo = [t for t in plan.tracks if not t.mbid and _worth_looking_up(plan, t)]
        for i, t in enumerate(todo, 1):
            progress(f"MusicBrainz: track {i}/{len(todo)} {t.artist} - {t.title}")
            stats["looked_up"] += 1
            stats["tracks"] += int(enrich_track(t, mb))
    except MusicBrainzError as e:
        log.warning("MusicBrainz unavailable, continuing without it: %s", e)
    refresh_derived(plan)
    return stats


def _worth_looking_up(plan: AlbumPlan, t: PlanTrack) -> bool:
    if Provenance.USER in t.provenance.values():
        return False
    # YouTube Music data is already good for albums; in compilations MB still fixes spelling
    return Provenance.YT_TITLE in t.provenance.values() or plan.kind == Kind.COMPILATION


def _set(obj: AlbumPlan | PlanTrack, name: str, value: Any) -> None:
    setattr(obj, name, value)
    obj.provenance[name] = Provenance.MB
    obj.auto[name] = value
