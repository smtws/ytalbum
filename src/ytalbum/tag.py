"""Stage 7: write Vorbis comments + embedded cover into an Opus file."""

from __future__ import annotations

import base64
from pathlib import Path

from mutagen.flac import Picture
from mutagen.oggopus import OggOpus

from .models import AlbumPlan, PlanTrack


def image_mime(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def tag_file(path: Path, plan: AlbumPlan, track: PlanTrack, cover: bytes | None = None) -> None:
    audio = OggOpus(path)
    audio.delete()  # drop whatever yt-dlp/ffmpeg put there
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
    for key, value in tags.items():
        audio[key] = [value]

    if cover and (mime := image_mime(cover)):
        pic = Picture()
        pic.type = 3  # front cover
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = cover
        audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
    audio.save()
