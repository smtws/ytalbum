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
    "new", "premiere", "explicit", "mv", "360", "grad", "degree", "full", "fps", "60fps",
    "offizielles", "offizielle", "offizieller", "offiziell", "musikvideo", "musikclip",
    "oficial", "officiel", "ufficiale",  # the same label in other languages
    # resolutions: a video fact, never an audio one - unlike "(Remaster)", which stays
    "1080p", "720p", "480p", "2160p", "1440p", "360p", "240p", "144p", "uhd", "fullhd",
}
# ...but only when the group clearly labels the video (so "(Music of the Night)" stays intact)
MARKER_WORDS = {
    "official", "offizielles", "offizielle", "offizieller", "offiziell", "video", "videoclip",
    "clip", "visualizer", "visualiser", "lyric", "lyrics", "audio", "mv", "hd", "hq", "4k", "8k",
    "musikvideo", "musikclip", "oficial", "officiel", "ufficiale",
    "1080p", "720p", "480p", "2160p", "1440p", "360p", "240p", "144p", "uhd", "fullhd",
}
_BRACKETS = re.compile(r"\s*[(\[【]([^()\[\]【】]*)[)\]】]")
# a trailing segment naming a publisher: "… / Napalm Records". Unlike "|", a slash appears in
# real titles ("Intro / Outro", "AC/DC"), so the words have to say it is a label.
PUBLISHER_WORDS = {"records", "record", "recordings", "entertainment", "productions", "publishing", "media", "label", "musikverlag"}
_SLASH = re.compile(r"\s+/\s+")
# A dash separates when spaced on both sides, or - "Arcana- Innocent Child" - when what
# follows it starts a name: a German compound ellipsis continues in lowercase ("sang- und
# klanglos") and must stay whole. A colon separates too ("Metallica: Nothing Else Matters").
_SEPARATOR = re.compile(r"(?:\s+[-–—~]{1,2}\s+|[-–—~]{1,2}\s+(?=[A-ZÀ-ÖØ-Þ])|\s*:\s+)")
_QUOTED = re.compile(r'^(?P<artist>[^"“”„\']+?)\s*["“„\'](?P<title>[^"“”\']+)["”“\'](?P<rest>.*)$')
_LEADING_QUOTED = re.compile(r'^["“„](?P<title>[^"“”]+)["”“](?P<rest>.*)$')
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")  # bidi/zero-width marks
_BY = re.compile(r"^(?P<title>.+?)\s+by\s+(?P<artist>[^()\[\]]+)$", re.I)


def before_label(text: str) -> str:
    """Drop a trailing "| Label", but only when the pipe stands outside every bracket.

    "Der Derwisch (Saltatio Mortis | Reading, Yoga & RPG Music)" keeps its bracket; cutting
    there used to leave it hanging open.
    """
    depth = 0
    for i, c in enumerate(text):
        if c in "([【":
            depth += 1
        elif c in ")]】":
            depth = max(0, depth - 1)
        elif depth == 0 and c == "|" and text[i - 1 : i] == " " and text[i + 1 : i + 2] == " ":
            return text[:i].rstrip()
    return text


def drop_label(text: str) -> str:
    """'Viva Vendetta | Napalm Records' -> 'Viva Vendetta': the uploader's label is not a name."""
    return _drop_publisher(before_label(clean_text(text))).strip(" -–—~")


def clean_text(text: str) -> str:
    """NFC, and without the invisible marks YouTube titles carry ('In The Nursery \u200e- …')."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFC", text))


def title_by_artist(title: str) -> tuple[str, str] | None:
    """'No Sound But The Wind by The Editors' -> ('The Editors', 'No Sound But The Wind').

    Only for titles that name no artist otherwise - plenty of songs have 'by' in them.
    """
    m = _BY.match(title.strip())
    return (m["artist"].strip(), m["title"].strip()) if m else None


def strip_album_name(album: str, title: str) -> str:
    """Take the release name out of a track title, wherever the shop put it.

    "1 - Der Kuss des Kometen (Teil 01)" -> "Teil 01"
    "Kapitel 01: Die Hexenmeister des Metal (Folge 4)" -> "Kapitel 01"

    Only a run of at least two words counts, so a single-word album keeps its title track,
    and a title that would end up empty is left alone. Whether this is applied at all is an
    album-wide decision (`drop_album_name`) - alone it would turn "Carolus Rex (Swedish
    version)" into "Swedish version".
    """
    # NB not casefolded: casefold() maps "ß" to "ss", and the pattern is matched (case
    # insensitively) against the original title, where the "ß" is still there
    words = re.findall(r"\w+", album or "")
    if len(words) < 2 or not title:
        return title
    vocabulary = _words_of(album)
    rest = title
    for n in range(len(words), 1, -1):
        run = r"\b" + r"\W+".join(map(re.escape, words[-n:])) + r"\b"  # \b: a bare "4" must not match inside "04"
        if re.search(run, rest, flags=re.I):
            rest = re.sub(run, "", rest, flags=re.I)
            break
    else:
        return title

    # "(Folge 4)" after the name is the release again, in brackets
    # a group that named the release, and the empty pair left when it sat inside one
    rest = _BRACKETS.sub(lambda m: "" if not _words_of(m[1]) or _words_of(m[1]) <= vocabulary else m[0], rest)
    rest = re.sub(r"^\W*\d+\W+", " ", rest) if _words_of(rest) - vocabulary else rest  # a leading "2 - "
    rest = re.sub(r"\s+", " ", rest).strip(" -–—:|,.")
    inner = _BRACKETS.fullmatch(rest)
    rest = (inner[1] if inner else rest).strip()
    return rest or title


def _words_of(text: str) -> set[str]:
    return {w.casefold() for w in re.findall(r"\w+", text or "")}


def strip_leading_artist(artist: str, title: str) -> str:
    """'Metallica: Nothing Else Matters' with artist Metallica -> 'Nothing Else Matters'."""
    if not artist or not title:
        return title
    parts = _SEPARATOR.split(title, maxsplit=1)
    if len(parts) == 2 and parts[1].strip() and key(parts[0]).startswith(key(artist)):
        return parts[1].strip()
    return title


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


def strip_self_feat(artist: str, title: str) -> str:
    """'Gary Jules' - 'Mad World (feat. Gary Jules)': the guest is the artist. Drop the credit."""
    if not artist:
        return title

    def drop(m: re.Match[str]) -> str:
        guests = _FEAT_WORD.split(m[1], maxsplit=1)
        return "" if len(guests) == 2 and key(guests[1]) == key(artist) else m[0]

    out = _BRACKETS.sub(drop, title)
    if (m := _FEAT_TAIL.search(out)) and key(m["guests"]) == key(artist):
        out = out[: m.start()]
    return re.sub(r"\s+", " ", out).strip(" -–—~")


def key(s: str) -> str:
    """Comparison key: case- and punctuation-insensitive."""
    return re.sub(r"\W+", "", s.casefold())


def channel_artist(channel: str | None) -> str | None:
    """'Mantus - Topic' -> 'Mantus', 'LACRIMOSAofficial' -> 'LACRIMOSA', 'SabatonVEVO' -> 'Sabaton'."""
    if not channel:
        return None
    name = clean_text(channel).strip()
    while (stripped := _CHANNEL_NOISE.sub("", name).strip()) != name:
        name = stripped
    return name or None


def clean_title(title: str) -> str:
    """Drop label suffixes, noise brackets like '(Official Video)', stray quotes and spacing."""
    title = before_label(clean_text(title))
    title = _drop_publisher(title)
    title = _BRACKETS.sub(_clean_group, title)
    parts = _SEPARATOR.split(title)
    while len(parts) > 1 and _is_noise(parts[-1]):  # "Song – Official Lyric Video"
        parts.pop()
    title = " - ".join(parts) if len(parts) > 1 else parts[0]
    title = _drop_noise_tail(title)  # "Gloria Offizielles Musikvideo", with no bracket or dash
    title = title.replace("@", "")  # "(feat. @handle)" -> "(feat. handle)"
    title = re.sub(r"\s+", " ", title).strip(" -–—~")
    if len(title) > 1 and title[0] in "\"“„'" and title[-1] in "\"”“'":
        title = title[1:-1].strip()
    return title


def parse_video_title(title: str, channel: str | None) -> tuple[str | None, str]:
    """Return (artist or None, song title). None means the title names no artist."""
    ch = channel_artist(channel)
    text = before_label(clean_text(title))

    parts = _SEPARATOR.split(text, maxsplit=1)
    # '"Mad World" (feat. Gary Jules) - Official Music Video': what follows the dash only
    # labels the video, so the whole text is the song - it names no artist.
    if len(parts) == 2 and not _is_noise(parts[1]):
        left, right = clean_title(parts[0]), clean_title(parts[1])
        if ch and key(right) == key(ch) and key(left) != key(ch):
            left, right = right, left  # "Song - Artist" where the channel tells us the artist
        return _prefer_channel_spelling(left, ch), right

    if m := _QUOTED.match(text):
        return _prefer_channel_spelling(clean_title(m["artist"]), ch), clean_title(m["title"])

    cleaned = clean_title(text)
    if m := _LEADING_QUOTED.match(cleaned):  # nothing before the quoted song: no artist in the title
        return None, clean_title(f"{m['title']}{m['rest']}")

    return None, cleaned


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


def _drop_publisher(title: str) -> str:
    """'U-Gra (Tagelharpa playthrough) / Napalm Records' -> without the label."""
    parts = _SLASH.split(title)
    while len(parts) > 1 and PUBLISHER_WORDS.intersection(re.findall(r"\w+", parts[-1].casefold())):
        parts.pop()
    return " / ".join(parts)


def _drop_noise_tail(title: str) -> str:
    """Trailing video-label words that carry no punctuation: 'Strange World HD 1080p'.

    The whole trailing run of noise words is judged together and dropped only if one of them
    labels the video: "Full HD" goes, while "Life Is Full" keeps its last word, because
    "full" alone labels nothing.
    """
    words = title.split()
    plain = [re.sub(r"\W", "", w).casefold() for w in words]
    cut = len(words)
    while cut > 1 and plain[cut - 1] in NOISE_WORDS:
        cut -= 1
    if cut == len(words) or not any(w in MARKER_WORDS for w in plain[cut:]):
        return title
    return " ".join(words[:cut])


def _is_noise(group: str) -> bool:
    words = re.findall(r"\w+", group.casefold())
    return bool(MARKER_WORDS.intersection(words)) and all(w in NOISE_WORDS for w in words)
