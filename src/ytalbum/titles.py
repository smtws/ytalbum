"""Derive artist + song title from a YouTube video title and its channel (DESIGN.md §5).

Pure string work, no lookups. What cannot be decided from the text alone
(e.g. "Song - Artist" in reverse order on a lyrics channel) is left for the
MusicBrainz step or the user.
"""

from __future__ import annotations

import re

# bracket groups made only of these words are video noise, not part of the song title
NOISE_WORDS = {
    "official", "music", "video", "videoclip", "clip", "lyric", "lyrics", "with",
    "visualizer", "visualiser", "audio", "hd", "hq", "4k", "8k", "upgrade", "upgraded",
    "new", "premiere", "explicit", "mv",
}
_BRACKETS = re.compile(r"\s*[(\[【]([^()\[\]【】]*)[)\]】]")
_SEPARATOR = re.compile(r"\s+[-–—~]{1,2}\s+")
_QUOTED = re.compile(r'^(?P<artist>[^"“”„]+?)\s*["“„](?P<title>[^"“”]+)["”“](?P<rest>.*)$')
_CHANNEL_NOISE = re.compile(r"(\s*-\s*topic|vevo|\s*official)$", re.I)


def key(s: str) -> str:
    """Comparison key: case- and punctuation-insensitive."""
    return re.sub(r"\W+", "", s.casefold())


def channel_artist(channel: str | None) -> str | None:
    """'Mantus - Topic' -> 'Mantus', 'LACRIMOSAofficial' -> 'LACRIMOSA', 'SabatonVEVO' -> 'Sabaton'."""
    if not channel:
        return None
    name = channel.strip()
    while (stripped := _CHANNEL_NOISE.sub("", name).strip()) != name:
        name = stripped
    return name or None


def clean_title(title: str) -> str:
    """Drop '| Label' suffixes, noise brackets like '(Official Video)', stray quotes and spacing."""
    title = title.split(" | ")[0]
    title = _BRACKETS.sub(lambda m: "" if _is_noise(m[1]) else m[0], title)
    title = title.replace("@", "")  # "(feat. @handle)" -> "(feat. handle)"
    title = re.sub(r"\s+", " ", title).strip(" -–—~")
    if len(title) > 1 and title[0] in "\"“„'" and title[-1] in "\"”“'":
        title = title[1:-1].strip()
    return title


def parse_video_title(title: str, channel: str | None) -> tuple[str | None, str]:
    """Return (artist or None, song title). None means the title names no artist."""
    ch = channel_artist(channel)
    text = title.split(" | ")[0]

    parts = _SEPARATOR.split(text, maxsplit=1)
    if len(parts) == 2:
        left, right = clean_title(parts[0]), clean_title(parts[1])
        if ch and key(right) == key(ch) and key(left) != key(ch):
            left, right = right, left  # "Song - Artist" where the channel tells us the artist
        return _prefer_channel_spelling(left, ch), right

    if m := _QUOTED.match(text):
        return _prefer_channel_spelling(clean_title(m["artist"]), ch), clean_title(m["title"])

    return None, clean_title(text)


def _prefer_channel_spelling(artist: str, ch: str | None) -> str:
    # channels are usually spelled properly ("Sabaton"), titles often shout ("SABATON")
    return ch if ch and key(ch) == key(artist) else artist


def _is_noise(group: str) -> bool:
    words = re.findall(r"\w+", group.casefold())
    return bool(words) and all(w in NOISE_WORDS for w in words)
