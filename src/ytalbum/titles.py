"""Derive artist + song title from a YouTube video title and its channel (DESIGN.md §5).

Pure string work, no lookups. What cannot be decided from the text alone
(e.g. "Song - Artist" in reverse order on a lyrics channel) is left for the
MusicBrainz step or the user.
"""

from __future__ import annotations

import re
import unicodedata

# bracket groups made only of these words are video noise, not part of the song title
NOISE_WORDS = {
    "official", "music", "video", "videoclip", "clip", "lyric", "lyrics", "with",
    "visualizer", "visualiser", "audio", "hd", "hq", "4k", "8k", "upgrade", "upgraded",
    "new", "premiere", "explicit", "mv", "360", "grad", "degree",
    "offizielles", "offizielle", "offizieller", "offiziell",
}
# ...but only when the group clearly labels the video (so "(Music of the Night)" stays intact)
MARKER_WORDS = {
    "official", "offizielles", "offizielle", "offizieller", "offiziell", "video", "videoclip",
    "clip", "visualizer", "visualiser", "lyric", "lyrics", "audio", "mv", "hd", "hq", "4k", "8k",
}
_BRACKETS = re.compile(r"\s*[(\[【]([^()\[\]【】]*)[)\]】]")
_SEPARATOR = re.compile(r"\s+[-–—~]{1,2}\s+")
_QUOTED = re.compile(r'^(?P<artist>[^"“”„]+?)\s*["“„](?P<title>[^"“”]+)["”“](?P<rest>.*)$')
_CHANNEL_NOISE = re.compile(r"(\s*-\s*topic|vevo|\s*official)$", re.I)


def natural_key(text: str) -> list[object]:
    """Sort key that reads digit runs as numbers: Vol. 2 before Vol. 10.

    Punctuation and spacing inside the words are ignored, so "Vol.9" and "Vol. 10" are
    ordered by their number, not by the dot.
    """
    parts = re.split(r"(\d+)", text or "")
    return [(1, int(p), "") if p.isdigit() else (0, 0, re.sub(r"\W+", " ", p).strip().casefold()) for p in parts]


_FEAT_WORD = re.compile(r"\b(?:feat\.?|ft\.?|featuring)\s", re.I)
_FEAT_TAIL = re.compile(r"\s*[(\[]?\s*\b(feat\.?|ft\.?|featuring)\s+(?P<guests>[^)\]]+?)\s*[)\]]?\s*$", re.I)


def split_feat(artist: str) -> tuple[str, str | None]:
    """'Feuerschwanz ft. Melissa Bonny' -> ('Feuerschwanz', 'ft. Melissa Bonny').

    Guest credits belong in the title; the artist field stays the performer, so the library
    does not grow an entry per collaboration.
    """
    m = _FEAT_TAIL.search(artist)
    if not m or not m["guests"].strip():
        return artist, None
    main = artist[: m.start()].strip(" -–—,&")
    return (main or artist), (None if not main else f"{m[1]} {m['guests'].strip()}")


def move_feat(artist: str, title: str) -> tuple[str, str]:
    """Take a guest credit out of the artist and append it to the title, once."""
    main, guests = split_feat(artist)
    if not guests:
        return artist, title
    if _FEAT_WORD.search(title):  # the title already names them, anywhere in it
        return main, title
    return main, f"{title} {guests}"


def key(s: str) -> str:
    """Comparison key: case- and punctuation-insensitive."""
    return re.sub(r"\W+", "", s.casefold())


def channel_artist(channel: str | None) -> str | None:
    """'Mantus - Topic' -> 'Mantus', 'LACRIMOSAofficial' -> 'LACRIMOSA', 'SabatonVEVO' -> 'Sabaton'."""
    if not channel:
        return None
    name = unicodedata.normalize("NFC", channel).strip()
    while (stripped := _CHANNEL_NOISE.sub("", name).strip()) != name:
        name = stripped
    return name or None


def clean_title(title: str) -> str:
    """Drop '| Label' suffixes, noise brackets like '(Official Video)', stray quotes and spacing."""
    title = unicodedata.normalize("NFC", title).split(" | ")[0]
    title = _BRACKETS.sub(_clean_group, title)
    parts = _SEPARATOR.split(title)
    while len(parts) > 1 and _is_noise(parts[-1]):  # "Song – Official Lyric Video"
        parts.pop()
    title = " - ".join(parts) if len(parts) > 1 else parts[0]
    title = title.replace("@", "")  # "(feat. @handle)" -> "(feat. handle)"
    title = re.sub(r"\s+", " ", title).strip(" -–—~")
    if len(title) > 1 and title[0] in "\"“„'" and title[-1] in "\"”“'":
        title = title[1:-1].strip()
    return title


def parse_video_title(title: str, channel: str | None) -> tuple[str | None, str]:
    """Return (artist or None, song title). None means the title names no artist."""
    ch = channel_artist(channel)
    text = unicodedata.normalize("NFC", title).split(" | ")[0]

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
    # channels are usually spelled properly ("Sabaton" vs a shouted "SABATON" title),
    # unless the channel name is all lowercase ("wardruna")
    return ch if ch and key(ch) == key(artist) and not ch.islower() else artist


def _clean_group(m: re.Match[str]) -> str:
    """'(Official Video)' -> '', '(Official Live Video)' -> ' (Live)', '(feat. X)' unchanged."""
    words = m[1].split()
    norm = [re.sub(r"\W", "", w).casefold() for w in words]
    if not MARKER_WORDS.intersection(norm):
        return m[0]
    kept = [w for w, n in zip(words, norm) if n not in NOISE_WORDS]
    if not kept:
        return ""
    opening, closing = m[0].strip()[0], m[0].strip()[-1]
    return f" {opening}{' '.join(kept)}{closing}"


def _is_noise(group: str) -> bool:
    words = re.findall(r"\w+", group.casefold())
    return bool(MARKER_WORDS.intersection(words)) and all(w in NOISE_WORDS for w in words)
