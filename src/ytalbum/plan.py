"""Pipeline stages 3-5: classify, derive metadata, build the AlbumPlan. Pure, no I/O."""

from __future__ import annotations

import re
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
        show_artist = kind == Kind.COMPILATION or _key(artist) != _key(albumartist)
        tracks.append(
            PlanTrack(
                video_id=entry.video_id,
                number=number,
                artist=artist,
                title=title,
                filename=track_filename(albumartist, album, number, artist if show_artist else None, title),
                provenance={"artist": artist_prov, "title": title_prov},
            )
        )

    skipped = [
        {"video_id": e.video_id, "title": e.title, "reason": reason}
        for e in collection.entries
        if (reason := skip_reason(e, collection))
    ]
    return AlbumPlan(
        source_url=collection.source_url,
        source_id=collection.source_id,
        kind=kind,
        album=album,
        albumartist=albumartist,
        year=year,
        cover_url=collection.thumbnail or (entries[0].thumbnail if entries else None),
        folder=f"{safe_name(albumartist)}/{safe_name(album)}",
        tracks=tracks,
        skipped=skipped,
        provenance=album_prov,
    )


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


def track_filename(albumartist: str, album: str, number: int, artist: str | None, title: str) -> str:
    """v1's convention: 'AlbumArtist - Album - NN - [TrackArtist - ]Title.opus'."""
    middle = f"{artist} - {title}" if artist else title
    stem = safe_name(f"{albumartist} - {album} - {number:02d} - {middle}")
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
