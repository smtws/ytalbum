"""Pipeline stages 3-5: classify, derive metadata, build the AlbumPlan. Pure, no I/O."""

from __future__ import annotations

import re
import copy
from collections import Counter

from .models import AlbumPlan, Collection, Entry, Kind, PlanTrack, Provenance
from .titles import NOISE_WORDS, channel_artist, key, parse_video_title

MIN_TRACK_SECONDS = 30  # shorter entries are intro cards, not songs (DESIGN.md §3.4)


# -- which entries become tracks -----------------------------------------------------


def skip_reason(entry: Entry, collection: Collection) -> str | None:
    if entry.skipped:
        return entry.skipped
    if entry.duration is not None and entry.duration < MIN_TRACK_SECONDS:
        return f"shorter than {MIN_TRACK_SECONDS}s ({entry.duration:.0f}s)"
    return None


def usable_entries(collection: Collection) -> list[Entry]:
    return [e for e in collection.entries if skip_reason(e, collection) is None]


# -- track-level metadata ------------------------------------------------------------


def track_artist(entry: Entry) -> tuple[str, Provenance]:
    """YouTube Music's field, else the artist named in the title, else the channel."""
    if entry.music.artist:
        return entry.music.artist, Provenance.YT_MUSIC
    parsed, _ = parse_video_title(entry.title, entry.channel)
    return parsed or channel_artist(entry.channel) or "Unknown Artist", Provenance.YT_TITLE


def track_title(entry: Entry) -> tuple[str, Provenance]:
    if entry.music.track:
        return entry.music.track, Provenance.YT_MUSIC
    return parse_video_title(entry.title, entry.channel)[1], Provenance.YT_TITLE


# -- stage 3: classify ---------------------------------------------------------------


def classify(collection: Collection) -> Kind:
    if not collection.is_playlist:
        return Kind.SINGLE
    if collection.source_id.startswith("OLAK5uy_"):
        return Kind.OFFICIAL_ALBUM
    artists = {_key(track_artist(e)[0]) for e in usable_entries(collection)}
    return Kind.ARTIST_PLAYLIST if len(artists) <= 1 else Kind.COMPILATION


# -- stages 4+5: metadata and plan ---------------------------------------------------


def build_plan(collection: Collection, kind: Kind | None = None) -> AlbumPlan:
    kind = kind or classify(collection)
    entries = usable_entries(collection)
    album_prov: dict[str, str] = {}

    if kind == Kind.COMPILATION:
        albumartist = collection.channel or "Various Artists"
        album = compilation_album_title(collection.title, albumartist)
        album_prov = {"albumartist": Provenance.PLAYLIST, "album": Provenance.PLAYLIST}
        year = None
    else:
        albumartist, prov = _most_common([track_artist(e) for e in entries]) or (
            collection.channel or "Unknown Artist",
            Provenance.PLAYLIST,
        )
        album_prov["albumartist"] = prov
        shared_album = _shared([e.music.album for e in entries])
        if shared_album:
            album, album_prov["album"] = shared_album, Provenance.YT_MUSIC
        else:
            album = _playlist_album_title(collection.title, albumartist)
            album_prov["album"] = Provenance.PLAYLIST
        # release_year also exists on plain videos (upload year) - only trust it with an album
        year = _most_common_value([e.music.year for e in entries if e.music.year and e.music.album])
        if year:
            album_prov["year"] = Provenance.YT_MUSIC

    tracks = []
    for number, entry in enumerate(entries, start=1):
        artist, artist_prov = track_artist(entry)
        title, title_prov = track_title(entry)
        tracks.append(
            PlanTrack(
                video_id=entry.video_id,
                number=number,
                artist=artist,
                title=title,
                filename="",  # set by refresh_derived
                provenance={"artist": artist_prov, "title": title_prov},
                auto={"artist": artist, "title": title},
                channel=entry.channel,
                duration=entry.duration,
            )
        )

    skipped = [
        {"video_id": e.video_id, "title": e.title, "reason": reason} | ({"transient": True} if e.transient else {})
        for e in collection.entries
        if (reason := skip_reason(e, collection))
    ]
    plan = AlbumPlan(
        source_url=collection.source_url,
        source_id=collection.source_id,
        kind=kind,
        album=album,
        albumartist=albumartist,
        year=year,
        cover_url=collection.thumbnail or (entries[0].thumbnail if entries else None),
        folder="",  # set by refresh_derived
        tracks=tracks,
        skipped=skipped,
        provenance=album_prov,
        auto={"kind": kind, "album": album, "albumartist": albumartist, "year": year},
    )
    return refresh_derived(plan)


def wanted_folder(plan: AlbumPlan) -> str:
    """Where the album belongs, relative to the library root, given its (edited) fields."""
    return f"{safe_name(plan.albumartist)}/{safe_name(plan.album)}"


def wanted_filename(plan: AlbumPlan, t: PlanTrack) -> str:
    show_artist = plan.kind == Kind.COMPILATION or _key(t.artist) != _key(plan.albumartist)
    disc = t.disc if max(x.disc for x in plan.tracks) > 1 else None
    return track_filename(plan.albumartist, plan.album, t.number, t.artist if show_artist else None, t.title, disc)


def refresh_derived(plan: AlbumPlan) -> AlbumPlan:
    """Update names of things not on disk yet. `folder` and the filenames of finished tracks
    describe what IS on disk; only the executor moves those (download.relocate / download.run)."""
    if not any(t.state == "done" for t in plan.tracks):
        plan.folder = wanted_folder(plan)  # nothing on disk yet: follow the (enriched) names
    for t in plan.tracks:
        if t.state != "done":
            t.filename = wanted_filename(plan, t)
    return plan


ALBUM_FIELDS = ("kind", "album", "albumartist", "year")
TRACK_FIELDS = ("artist", "title")


def merge_plans(existing: AlbumPlan, fresh: AlbumPlan) -> AlbumPlan:
    """Bring an existing plan up to date with a fresh one from the same source (DESIGN.md slice 3).

    - user edits (value differs from the recorded auto value) always win;
      untouched fields take the fresh auto value
    - track order and numbers follow the source (for a curated playlist the order is the
      content; for a matched release, MusicBrainz' numbering), so a video that shows up
      later lands where it belongs, not at the end
    - tracks that left the source are kept (their files stay), flagged and put last
    """
    merged = copy.deepcopy(existing)
    _merge_fields(merged, fresh, ALBUM_FIELDS)
    merged.cover_url = fresh.cover_url or merged.cover_url
    merged.cover_fallback_url = fresh.cover_fallback_url or merged.cover_fallback_url
    merged.mbid = fresh.mbid or merged.mbid
    merged.skipped = fresh.skipped

    fresh_by_id = {t.video_id: t for t in fresh.tracks}
    listed = set(fresh_by_id) | {s["video_id"] for s in fresh.skipped}  # skipped videos are still in the source
    known = set()
    for t in merged.tracks:
        known.add(t.video_id)
        f = fresh_by_id.get(t.video_id)
        t.in_source = t.video_id in listed
        if f:
            _merge_fields(t, f, TRACK_FIELDS)
            if t.provenance.get("title") != Provenance.USER:
                t.mbid = f.mbid or t.mbid
            t.channel = f.channel or t.channel
            t.mb_length = f.mb_length or t.mb_length
            t.duration = f.duration or t.duration

    for f in fresh.tracks:
        if f.video_id not in known:
            merged.tracks.append(copy.deepcopy(f))

    # numbering: the fresh plan's, then everything no longer in it, in its previous order
    by_id = {t.video_id: t for t in merged.tracks}
    ordered = [by_id[f.video_id] for f in fresh.tracks]
    rest = sorted((t for t in merged.tracks if t.video_id not in fresh_by_id), key=lambda t: (t.disc, t.number))
    for t, f in zip(ordered, fresh.tracks):
        t.number, t.disc = f.number, f.disc
    last = max((f.number for f in fresh.tracks), default=0)
    for i, t in enumerate(rest, 1):
        t.number, t.disc = last + i, max((f.disc for f in fresh.tracks), default=1)
    merged.tracks = ordered + rest
    return refresh_derived(merged)


# how much a value is trusted; a merge never replaces a value by a less trusted one
TRUST = {Provenance.PLAYLIST: 1, Provenance.YT_TITLE: 1, Provenance.YT_MUSIC: 2, Provenance.MB: 3, Provenance.USER: 4}


def _merge_fields(target: AlbumPlan | PlanTrack, fresh: AlbumPlan | PlanTrack, fields: tuple[str, ...]) -> None:
    for name in fields:
        if _edited(target, name):
            target.provenance[name] = Provenance.USER
        elif TRUST.get(fresh.provenance.get(name), 0) < TRUST.get(target.provenance.get(name), 0):
            continue  # e.g. MusicBrainz was skipped or down this time: keep what it said before
        else:
            setattr(target, name, getattr(fresh, name))
            if name in fresh.provenance:
                target.provenance[name] = fresh.provenance[name]
            else:
                target.provenance.pop(name, None)
        target.auto[name] = fresh.auto.get(name)


def _edited(obj: AlbumPlan | PlanTrack, name: str) -> bool:
    return name in obj.auto and getattr(obj, name) != obj.auto[name]


def compilation_album_title(playlist_title: str, curator: str) -> str:
    """'My Dark Lullabies Vol.1 - Heavy Sleeping' -> 'Vol. 1 - Heavy Sleeping' (DESIGN.md §2.1)."""
    title = playlist_title.strip()
    words = re.findall(r"\w+", curator)
    if words:  # match the curator's words with any spacing/punctuation between them
        prefix = r"\W*".join(map(re.escape, words))
        title = re.sub(rf"^{prefix}\b[\s\-–—:|]*", "", title, flags=re.I)
    title = re.sub(r"^vol(?:ume)?\.?\s*(\d+)\s*[-–—:|]\s*", r"Vol. \1 - ", title, flags=re.I)
    return title or playlist_title.strip()


ALBUM_NOISE = NOISE_WORDS | {"full", "album", "complete", "playlist", "stream"}


def _playlist_album_title(playlist_title: str, artist: str) -> str:
    """'SABATON - Legends (Full Album)' -> 'Legends'; 'Album - Chronik' -> 'Chronik'."""
    title = playlist_title.strip().removeprefix("Album - ")
    for sep in (" - ", " – ", ": "):
        head, found, rest = title.partition(sep)
        if found and _key(head) == _key(artist):
            title = rest.strip()
            break
    cleaned = re.sub(
        r"\s*[(\[]([^()\[\]]*)[)\]]",
        lambda m: "" if set(re.findall(r"\w+", m[1].casefold())) <= ALBUM_NOISE else m[0],
        title,
    ).strip()
    return cleaned or title


# -- filenames -----------------------------------------------------------------------

_UNSAFE = str.maketrans({"/": "-", "\\": "-", "|": "-", ":": " -", "*": "", "?": "", "<": "", ">": "", '"': "'"})
MAX_NAME_BYTES = 240


def safe_name(name: str) -> str:
    name = "".join(ch for ch in name.translate(_UNSAFE) if ch.isprintable())
    name = re.sub(r"\s+", " ", name).strip(" .")
    while len(name.encode()) > MAX_NAME_BYTES:
        name = name[:-1].rstrip(" .")
    return name or "_"


def track_filename(
    albumartist: str, album: str, number: int, artist: str | None, title: str, disc: int | None = None
) -> str:
    """v1's convention: 'AlbumArtist - Album - [D-]NN - [TrackArtist - ]Title.opus'."""
    middle = f"{artist} - {title}" if artist else title
    num = f"{disc}-{number:02d}" if disc else f"{number:02d}"
    stem = safe_name(f"{albumartist} - {album} - {num} - {middle}")
    return f"{stem}.opus"


# -- small helpers -------------------------------------------------------------------


_key = key


def _most_common(pairs: list[tuple[str, Provenance]]) -> tuple[str, Provenance] | None:
    if not pairs:
        return None
    best_key, _ = Counter(_key(v) for v, _ in pairs).most_common(1)[0]
    return next(p for p in pairs if _key(p[0]) == best_key)


def _most_common_value[T](values: list[T]) -> T | None:
    return Counter(values).most_common(1)[0][0] if values else None


def _shared(values: list[str | None]) -> str | None:
    """The value if every entry has the same non-empty one."""
    present = {_key(v): v for v in values if v}
    return next(iter(present.values())) if len(present) == 1 and all(values) else None
