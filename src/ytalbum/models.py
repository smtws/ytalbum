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
class SourceRef:
    """A collection found on a channel, not fetched yet."""

    url: str
    source_id: str
    title: str
    tab: str  # "releases" (official albums), "playlists", "ytmusic" or "search"
    artist: str | None = None  # who YouTube says made it (search hits only)
    channel_url: str | None = None  # the channel that uploaded its tracks
    count: int | None = None  # entries, when YouTube told us
    thumbnail: str | None = None


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
    transient: bool = False  # the reason may go away (bot check, network): the entry is still in the source

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
    modified: str | None = None  # when YouTube last changed the playlist (YYYYMMDD)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Collection:
        entries = [Entry(**{**e, "music": Music(**e.get("music", {}))}) for e in d["entries"]]
        return cls(**{**d, "entries": entries})

    @property
    def unreadable(self) -> list[Entry]:
        """Entries that failed for a temporary reason: the collection is incomplete right now."""
        return [e for e in self.entries if e.transient]


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
    error_kind: str | None = None  # "no_audio_stream" → the UI can offer the choice
    audio_choice: str = "best"  # "best" = separate audio stream; "combined" = take it from the video
    ext: str = "opus"  # "m4a" when the audio was taken from a combined stream (copied, not re-encoded)
    # values as derived automatically; a field that differs from these was edited by the user
    auto: dict[str, str] = field(default_factory=dict)
    in_source: bool = True  # False once the video has left the source playlist
    tagged: str | None = None  # signature of the tags last written to the file
    mbid: str | None = None  # MusicBrainz recording id
    channel: str | None = None  # who uploaded it (for "trim everything from this channel")
    trim_start: float | None = None  # seconds cut from the front (label idents …)
    trim_end: float | None = None  # play only up to here (previews, outros)
    trimmed: str | None = None  # the trim actually applied to the file on disk
    mb_length: float | None = None  # seconds, as MusicBrainz knows the recording
    duration: float | None = None  # seconds of the video itself (YouTube)
    lyrics: str | None = None  # synced | plain | instrumental | none; None = not looked up yet
    lyrics_id: int | None = None  # the lrclib entry the text came from
    lyrics_length: float | None = None  # seconds of the recording lrclib matched (or refused on)
    lyrics_sha: str | None = None  # of the sidecar bytes *we* wrote; anything else is the user's
    file_length: float | None = None  # seconds of audio actually on disk, after any trim


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
    mbid: str | None = None  # MusicBrainz release id
    cover_fallback_url: str | None = None  # tried when cover_url fails (e.g. no Cover Art Archive image)
    cover_fetched: dict[str, str] = field(default_factory=dict)  # {url, sha1} of the cover.* we saved
    # what the source looked like last time: lets an update skip it after one cheap request
    source_state: dict[str, Any] = field(default_factory=dict)  # {"ids": [...], "modified": "YYYYMMDD"}
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
