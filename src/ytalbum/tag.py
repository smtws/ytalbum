"""Stage 7: write Vorbis comments + embedded cover into an Opus file."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from mutagen import MutagenError
from mutagen.flac import Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus

from .models import AlbumPlan, PlanTrack

PICTURE_KEY = "metadata_block_picture"


def image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def audio_length(path: Path) -> float | None:
    """Seconds of audio in the file — the trimmed truth, not what YouTube said."""
    try:
        audio = MP4(path) if path.suffix.lower() in (".m4a", ".mp4") else OggOpus(path)
        return float(audio.info.length)
    except (MutagenError, OSError):  # not readable, not audio: an unknown length means "no match"
        return None  # and nothing else is swallowed: a bug here must not read as a missing file


def tagged_lyrics(path: Path) -> str | None:
    """The words currently in the file's tags — what we last wrote there, if anyone."""
    try:
        if path.suffix.lower() in (".m4a", ".mp4"):
            return ((MP4(path).tags or {}).get("\xa9lyr") or [None])[0]
        return ((OggOpus(path).tags or {}).get("lyrics") or [None])[0]
    except (MutagenError, OSError):  # same rule as `audio_length`: only a file we cannot read
        return None


def build_tags(plan: AlbumPlan, track: PlanTrack, lyrics: str | None = None) -> dict[str, str]:
    tags = {
        "title": track.title,
        "artist": track.artist,
        "albumartist": plan.albumartist,
        "album": plan.album,
        "tracknumber": str(track.number),
        "tracktotal": str(len(plan.tracks)),
        "totaltracks": str(len(plan.tracks)),
        "youtube_id": track.video_id,
        "source": plan.source_url,
    }
    if plan.year:
        tags["date"] = str(plan.year)
    if plan.is_compilation:
        tags["compilation"] = "1"
    if max(t.disc for t in plan.tracks) > 1:
        tags["discnumber"] = str(track.disc)
    if plan.mbid:
        tags["musicbrainz_albumid"] = plan.mbid
    if track.mbid:
        tags["musicbrainz_trackid"] = track.mbid
    if lyrics:
        # one key, the one every tag-reading player understands; timestamps and all,
        # because that is what the .lrc beside the file holds (lyrics.py)
        tags["lyrics"] = lyrics
    return tags


def signature(plan: AlbumPlan, track: PlanTrack, cover: bytes | None, lyrics: str | None = None) -> str:
    """Changes whenever the tags or cover that tag_file would write change."""
    payload = json.dumps(build_tags(plan, track, lyrics), sort_keys=True).encode()
    payload += hashlib.sha1(cover or b"").digest()
    return hashlib.sha1(payload).hexdigest()[:16]


MP4_KEYS = {  # Vorbis comment -> MP4 atom
    "title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb",
    "date": "\xa9day", "source": "\xa9cmt", "lyrics": "\xa9lyr", "youtube_id": "----:com.apple.iTunes:YOUTUBE_ID",
    "musicbrainz_albumid": "----:com.apple.iTunes:MusicBrainz Album Id",
    "musicbrainz_trackid": "----:com.apple.iTunes:MusicBrainz Track Id",
}


def tag_file(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None = None, lyrics: str | None = None) -> str:
    """Replace all tags. Keeps an already embedded cover if no new one is given. Returns the signature.

    Lyrics are not kept but rewritten from the `.lrc` sidecar the caller read (lyrics.py).
    """
    if path.suffix.lower() in (".m4a", ".mp4"):
        return _tag_mp4(path, plan, track, cover, lyrics)
    audio = OggOpus(path)
    old_picture = (audio.tags or {}).get(PICTURE_KEY)
    audio.delete()  # drop whatever yt-dlp/ffmpeg or an earlier run put there
    for key, value in build_tags(plan, track, lyrics).items():
        audio[key] = [value]

    if cover and (mime := image_mime(cover)):
        pic = Picture()
        pic.type = 3  # front cover
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = cover
        audio[PICTURE_KEY] = [base64.b64encode(pic.write()).decode("ascii")]
    elif old_picture and cover is None:
        audio[PICTURE_KEY] = old_picture
    audio.save()
    return signature(plan, track, cover, lyrics)


def _tag_mp4(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None, lyrics: str | None = None) -> str:
    """Same tags for the .m4a files (audio copied out of a combined stream)."""
    audio = MP4(path)
    old_cover = audio.tags.get("covr") if audio.tags else None
    audio.delete()
    tags = build_tags(plan, track, lyrics)
    for key, atom in MP4_KEYS.items():
        if value := tags.get(key):
            audio[atom] = [value.encode() if atom.startswith("----") else value]
    audio["trkn"] = [(track.number, len(plan.tracks))]
    if max(t.disc for t in plan.tracks) > 1:
        audio["disk"] = [(track.disc, max(t.disc for t in plan.tracks))]
    audio["cpil"] = plan.is_compilation
    if cover and (mime := image_mime(cover)) in ("image/jpeg", "image/png"):
        fmt = MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG
        audio["covr"] = [MP4Cover(cover, imageformat=fmt)]
    elif old_cover and cover is None:
        audio["covr"] = old_cover
    audio.save()
    return signature(plan, track, cover, lyrics)
