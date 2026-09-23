"""Artist search (DESIGN.md slice 6): find an artist's albums without knowing any URL.

1. YouTube Music's album search → official OLAK5uy_ albums by that artist.
2. The channel that uploaded most of them is the artist's real channel (whatever it is
   called, e.g. "fauntube" for Faun) → its Releases tab is the complete discography.
3. Playlist search adds lyric-video and fan "full album" playlists as a separate group.
4. Optionally MusicBrainz' album list shows which studio albums were not found at all.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from .enrich import core
from .mb import MusicBrainzAPI, MusicBrainzError
from .models import SourceRef
from .titles import key

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    groups: list[tuple[str, list[SourceRef]]] = field(default_factory=list)
    channel_url: str | None = None
    missing: list[str] = field(default_factory=list)  # MB studio albums not found on YouTube

    @property
    def refs(self) -> list[SourceRef]:
        return [r for _, group in self.groups for r in group]


def by_artist(refs: list[SourceRef], artist: str) -> list[SourceRef]:
    """Hits credited to exactly this artist (as one of possibly several creators)."""
    want = key(artist)
    return [r for r in refs if r.artist and want in {key(a) for a in r.artist.split(",")} | {key(r.artist)}]


def main_channel(refs: list[SourceRef], artist: str) -> str | None:
    """The channel uploading most hits among those whose name contains the artist
    ("fauntube" for Faun; YouTube does not always say who the creators are)."""
    want = key(artist)
    counts = Counter(r.channel_url for r in refs if r.channel_url and r.artist and want in key(r.artist))
    return counts.most_common(1)[0][0] if counts else None


MIN_ALBUM_TRACKS = 5


def search_artist(yt, artist: str, mb: MusicBrainzAPI | None = None) -> SearchResult:
    result = SearchResult()
    hits = yt.search_albums(artist)
    result.channel_url = main_channel(hits, artist)
    exact = {r.source_id for r in by_artist(hits, artist)}
    albums = [r for r in hits if r.source_id in exact or (result.channel_url and r.channel_url == result.channel_url)]
    searched_raw = by_artist(yt.search_playlists(f"{artist} full album"), artist) + yt.search_playlists(f"{artist} album")
    if not result.channel_url:  # e.g. a playlist curator: no albums on YouTube Music, but its own playlists
        result.channel_url = main_channel(by_artist(searched_raw, artist), artist)
    channel_refs = yt.list_channel(result.channel_url) if result.channel_url else []

    mb_albums: list[str] = []
    if mb is not None:
        try:
            mb_albums = [g["title"] for g in mb.artist_albums(artist)]
        except MusicBrainzError as e:
            log.warning("MusicBrainz unavailable, no discography check: %s", e)

    seen: set[str] = set()

    def fresh(refs: list[SourceRef]) -> list[SourceRef]:
        out = [r for r in refs if r.source_id not in seen]
        seen.update(r.source_id for r in out)
        return out

    official = dedupe_by_title([r for r in channel_refs if r.tab == "releases"] + albums)
    album_keys = {key(core(t)) for t in mb_albums} | {key(core(r.title)) for r in official if (r.count or 0) >= MIN_ALBUM_TRACKS}
    full = fresh([r for r in official if key(core(r.title)) in album_keys])
    other = fresh(official)
    playlists = fresh([r for r in channel_refs if r.tab == "playlists"])
    searched = fresh(searched_raw)
    for label, refs in (
        ("Albums", full),
        ("Singles, EPs and other releases", other),
        ("Playlists on the artist's channel", playlists),
        ("Other playlists found by search", searched),
    ):
        if refs:
            result.groups.append((label, refs))

    have = {key(core(r.title)) for r in result.refs}
    result.missing = [t for t in mb_albums if not any(key(core(t)) in h for h in have)]
    return result


def dedupe_by_title(refs: list[SourceRef]) -> list[SourceRef]:
    """YouTube often keeps several playlists of one album; keep the first, fill in what it lacks.

    The Releases tab gives no cover and no track count, YouTube Music's album search does.
    """
    out: dict[str, SourceRef] = {}
    for r in refs:
        k = key(core(r.title))
        if k not in out:
            out[k] = r
            continue
        for name in ("count", "thumbnail", "artist", "channel_url"):  # not `field`: dataclasses.field
            if getattr(r, name) and not getattr(out[k], name):
                setattr(out[k], name, getattr(r, name))
    return list(out.values())
