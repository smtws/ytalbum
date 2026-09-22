"""Stage 7: write Vorbis comments + embedded cover into an Opus file."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from mutagen.flac import Picture
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


def build_tags(plan: AlbumPlan, track: PlanTrack) -> dict[str, str]:
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
    return tags


def signature(plan: AlbumPlan, track: PlanTrack, cover: bytes | None) -> str:
    """Changes whenever the tags or cover that tag_file would write change."""
    payload = json.dumps(build_tags(plan, track), sort_keys=True).encode()
    payload += hashlib.sha1(cover or b"").digest()
    return hashlib.sha1(payload).hexdigest()[:16]


def tag_file(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None = None) -> str:
    """Replace all tags. Keeps an already embedded cover if no new one is given. Returns the signature."""
    audio = OggOpus(path)
    old_picture = (audio.tags or {}).get(PICTURE_KEY)
    audio.delete()  # drop whatever yt-dlp/ffmpeg or an earlier run put there
    for key, value in build_tags(plan, track).items():
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
    return signature(plan, track, cover)
