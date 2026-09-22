"""The one data model (DESIGN.md §6).

A `Collection` is what YouTube says; an `AlbumPlan` is what we will write to disk.
Both are plain dataclasses that round-trip through JSON. "Entries in the playlist"
and "tracks on the album" are different lists and are never conflated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

PLAN_SCHEMA = 1


class Kind(StrEnum):
    OFFICIAL_ALBUM = "official_album"
    ARTIST_PLAYLIST = "artist_playlist"
    COMPILATION = "compilation"
    SINGLE = "single"


class Provenance(StrEnum):
    MB = "mb"
    YT_MUSIC = "yt_music"  # yt-dlp's artist/track/album fields
    YT_TITLE = "yt_title"  # derived from the video title / channel name
    PLAYLIST = "playlist"  # derived from the playlist title / owner
    USER = "user"


@dataclass
class Music:
    """yt-dlp's music metadata for one video. Only filled by full extraction."""

    artist: str | None = None
    track: str | None = None
    album: str | None = None
    year: int | None = None


@dataclass
class Entry:
    video_id: str
    position: int  # 1-based position in the source playlist
    title: str
    channel: str | None = None
    duration: float | None = None
    thumbnail: str | None = None
    chapters: list[dict[str, Any]] = field(default_factory=list)
    music: Music = field(default_factory=Music)
    skipped: str | None = None  # reason, if this entry is unusable

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass
class Collection:
    source_url: str
    source_id: str  # playlist id or video id
    is_playlist: bool
    title: str
    channel: str | None
    thumbnail: str | None
    fetched_at: str
    entries: list[Entry]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Collection:
        entries = [Entry(**{**e, "music": Music(**e.get("music", {}))}) for e in d["entries"]]
        return cls(**{**d, "entries": entries})


@dataclass
class PlanTrack:
    video_id: str
    number: int
    artist: str
    title: str
    filename: str
    provenance: dict[str, str]  # field name -> Provenance
    disc: int = 1
    state: str = "pending"  # pending | done | failed
    error: str | None = None
    # values as derived automatically; a field that differs from these was edited by the user
    auto: dict[str, str] = field(default_factory=dict)
    in_source: bool = True  # False once the video has left the source playlist
    tagged: str | None = None  # signature of the tags last written to the file


@dataclass
class AlbumPlan:
    source_url: str
    source_id: str
    kind: str
    album: str
    albumartist: str
    year: int | None
    cover_url: str | None
    folder: str  # relative to the library root
    tracks: list[PlanTrack]
    skipped: list[dict[str, str]] = field(default_factory=list)  # {video_id, title, reason}
    provenance: dict[str, str] = field(default_factory=dict)
    auto: dict[str, object] = field(default_factory=dict)  # album-level twin of PlanTrack.auto
    schema: int = PLAN_SCHEMA

    @property
    def is_compilation(self) -> bool:
        return self.kind == Kind.COMPILATION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AlbumPlan:
        if d.get("schema") != PLAN_SCHEMA:
            raise ValueError(f"unsupported plan schema {d.get('schema')!r} (expected {PLAN_SCHEMA})")
        return cls(**{**d, "tracks": [PlanTrack(**t) for t in d["tracks"]]})
