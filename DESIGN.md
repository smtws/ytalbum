# YT-Downloads v3 — Design

Status: draft, 2026-09-22. Replaces `ARCHITECTURE_PLAN.md` (v2) and the v1 tree in
`~/YT-Downloads-master`. Nothing from v1/v2 is carried over as code unless listed under
"Salvage" at the end.

## 1. Goal

Turn a YouTube *collection* into a properly tagged album on disk:

- **Input:** a URL (playlist, channel, video) — or later an artist search.
- **Output:** `<library_root>/<AlbumArtist>/<Album>/<AlbumArtist> - <Album> - NN - [TrackArtist - ]Title.opus`,
  tagged (artist, albumartist, album, title, tracknumber/total, disc if >1, date,
  embedded cover), without re-encoding YouTube's Opus stream.
- **Re-runnable:** running it again on the same source downloads only what is new
  (e.g. a growing playlist like My Dark Lullabies Vol. 20).

MusicBrainz is an **enricher, never a gatekeeper.** A collection with no MusicBrainz
entry is downloaded with the best metadata YouTube offers, which the user can edit before
downloading.

## 2. The three cases

| Kind | Example | Album-level data | Track-level data |
|---|---|---|---|
| `official_album` | YT Music album playlist (`OLAK5uy_…`), Topic channel release | MB release if matched, else YT Music fields | MB tracklist if matched, else per-video `track`/`artist` |
| `artist_playlist` | an artist's own playlist, a YouTube-only band | playlist title / channel | per-video music fields, else cleaned video title |
| `compilation` | My Dark Lullabies Vol. 1 (14 bands, 14 uploaders) | albumartist = curator, album = playlist title minus curator prefix (see §2.1) | per track: YT music fields → title parsing → MB recording lookup |
| (`chapter_album`) | one "full album" video with chapters | video title / channel | chapters |

### 2.1 Compilation naming (decided 2026-09-22)

- **albumartist = the curator** (the playlist's channel, e.g. `My Dark Lullabies`).
- **album = the playlist title with the curator prefix removed**:
  `My Dark Lullabies Vol. 1 - Heavy Sleeping` → `Vol. 1 - Heavy Sleeping`.
- The curator's own titles are inconsistent (`Vol.1`/`Vol. 1`, `-`/`–`), so the album
  title is normalised to `Vol. N - Title` with an ASCII hyphen, so all 20 volumes look
  and sort alike. Editable in the preview like any other field.
- Result: `Library/My Dark Lullabies/Vol. 1 - Heavy Sleeping/My Dark Lullabies - Vol. 1 - Heavy Sleeping - 01 - Enemy Inside - Lullaby.opus`
  (the `[TrackArtist - ]` part is always present for compilations).

### 2.2 Classification

Classification (deterministic, testable, overridable in the preview):
1. Playlist id starts with `OLAK5uy_` → `official_album`.
2. Single video with chapters → `chapter_album`; without chapters → single track.
3. Playlist whose entries resolve (after step 4.2) to one artist → `artist_playlist`,
   several artists → `compilation`.

## 3. Verified facts about yt-dlp (checked 2026-09-22, yt-dlp 2026.08.19)

These are the facts v1/v2 got wrong or never knew. Fixtures in `design-fixtures/`.

1. **`playlist_count` on a flat entry is the size of the list the entry was found in,
   not its own track count.** `ytsearch5:` → every video has 5; `@channel/playlists` →
   every playlist has 20. This single misread caused the whole v2 fix/break loop
   ("MB:11 / YT:46"). A playlist's real size is only known after fetching *that* playlist.
2. **Full (non-flat) extraction of a single video returns music metadata**: `artist`,
   `artists`, `track`, `album`, `release_year` — not only for Topic channels but also for
   band channels (Schandmaul "Prinzessin" → album "Anderswelt", 2008). Flat entries don't
   have these. This is the primary source for compilation tracks.
3. **Artist-channel "Full Album" playlists are not albums.** Sabaton "Legends (Full Album)"
   = 11 songs + feat. versions + the full-album video + 3 documentaries (17 entries; the
   old debug scripts expected 7). Playlists change over time → tests must use saved
   snapshots, never live data.
4. **Compilations contain non-songs**: Vol. 1 starts with a 3 s intro card uploaded by the
   curator. Rule: skip entries < 30 s. (NOT "title equals the playlist title": that also
   matches Sabaton's 45-min "Legends (Full Album)" video inside the Legends playlist.)
5. **yt-dlp now needs a JS runtime** ("No supported JavaScript runtime… some formats may be
   missing"). Only deno is enabled by default; node is installed here and needs
   `js_runtimes` config. Must be solved in setup, or formats (incl. Opus 251) may vanish.
6. Opus 251 (~148 kbps) exists for normal videos; download it and remux, never transcode.
7. Channel `/releases` tab exists only for official artist channels; a curator channel
   (MyDarkLullabies) has only `/playlists`. "Artist - Topic" channels have neither tab
   any more; YouTube Music's album search (`music.youtube.com/search?q=…#albums`) returns
   `MPREb_` ids that resolve to the `OLAK5uy_` playlists, whose tracks name the real
   uploading channel (Faun → "fauntube").
8. **A playlist may list the same video twice.** Mono Inc's "The Clock Ticks On" album
   playlist lists seven acoustic versions a second time. Both entries became tracks with
   the same wanted filename, overwrote each other and left the renamed older copies as
   orphans. A video is one track per album, in `usable_entries` and in `merge_plans`.
9. **YouTube's bot check.** After a few hundred requests in a day, every video answers
   "Sign in to confirm you're not a bot" while playlist listings still work. A run then
   sees a playlist whose entries all failed — and v3 briefly reclassified Vol. 1 from that
   partial view and moved it to `Erben der Schöpfung/…`. Rules since: failures are
   *transient* (bot check, 403/429/5xx, timeouts) or permanent (private, deleted,
   age-restricted); a skipped video is still *in* the source; any transient failure means
   "incomplete — change nothing"; the first bot check stops all further requests
   (fetch, download, `update`, multi-picks). Defaults: 2 parallel requests, 0.5 s apart.
   **A logged-in browser session helps:** with Firefox cookies
   (`cookies_from_browser = "firefox"`) the same day's full `update` ran without a single
   bot check. (Chrome cookies stopped working for the owner earlier — Chrome changed its
   cookie storage.)
10. **Age-restricted videos** become *readable* with a login, but an account that is not
   age-verified gets only format 18 (360p video, low-bitrate AAC); all audio-only streams
   are withheld or need a PO token. It is *not* the account: the same video plays in high
   resolution in that very Firefox, because the browser presents a proof-of-origin token.
   No yt-dlp client (tv, web_safari, mweb, web_embedded) gets audio without one.
   **Fix (2026-09-22):** the bgutil PO-token generator in script mode —
   `bgutil-ytdlp-pot-provider` (plugin, in the venv) + its generator built in
   `.pot-provider/server` (v2.0.0, Node, gitignored). ytalbum detects it and passes
   `youtubepot-bgutilscript:server_home`. Result: Opus 251 offered, Feuerschwanz track
   downloaded at 121 kbps. Script mode costs a Node process per request (a full 4-album
   update took 2.5 min), so the default is now **server mode** (`pot.py`): before reading
   or downloading, ytalbum pings `127.0.0.1:4416/ping` and, if nothing answers, starts a
   detached watchdog (`python -m ytalbum.pot`) running `node build/main.js` on localhost
   only; every YouTube request and download progress touches
   `~/.cache/ytalbum/pot-server.heartbeat`, and after `pot_idle` (300 s) without a beat the
   watchdog stops Node and exits. Script mode stays configured as the plugin's fallback.
   `pot_mode = "script" | "off"` in the config switches. v3 never transcodes the 360p
   fallback into Opus.

## 4. Pipeline

Each stage is a plain function: input → output, no shared mutable state, no network in
stages 3–5. Stages 1–2 do all YouTube I/O.

```
URL ─► 1 resolve ─► 2 inspect ─► 3 classify ─► 4 enrich ─► 5 plan ─► [preview/edit] ─► 6 download ─► 7 tag+place
```

1. **resolve(url)** → `Source` list. Channel URL → its playlists (and releases tab if
   any) as separate sources. Playlist → one source. Video → one source.
2. **inspect(source)** → `Collection` with real entries: fetch the playlist itself,
   then full-extract each entry (bounded concurrency, e.g. 4) to get duration, chapters,
   music fields, availability. Unavailable/private entries are recorded as `skipped` with
   a reason — never abort the collection.
3. **classify(collection)** → kind (§2). Pure.
4. **enrich(collection)** → proposed metadata, every field carrying its **provenance**
   (`mb`, `yt_music`, `yt_title`, `playlist`, `user`):
   - album level: MB release search only for `official_album`/`artist_playlist`;
     accept only if artist AND title match and track count is within ±1 of songs.
   - track level: YT music fields → title parser (§5) → optional MB recording lookup.
5. **plan(collection, metadata)** → `AlbumPlan`: folder, filenames, tags, cover source,
   list of video ids to download. Written as JSON next to the album
   (`.ytalbum.json`) — this is also the manifest for incremental re-runs.
6. **download(plan)** → Opus files, one at a time or small concurrency; each track is
   tagged and moved into place as soon as it finishes (interrupt-safe, v1's good idea).
7. **tag(file, track)** — mutagen `OggOpus`, cover as `METADATA_BLOCK_PICTURE`.

The user can stop after step 5 (`--dry-run`), edit the plan, and run step 6–7 from it.

## 5. Title parsing (compilation / YouTube-only)

Order of trust for a track's artist + title:
1. yt-dlp `artist`/`track` fields (§3.2).
2. Channel is `<X> - Topic` → artist = X, title = video title.
3. Channel name ≈ first part of `A - B` title → artist = A.
4. Parse `A - B` / `A "B"` / `A – B`; strip noise: `(Official Video)`, `(Official Music Video)`,
   `(Official Lyric Video)`, `(Official Visualizer)`, `[4K UPGRADE]`, `(LYRICS)`,
   `| <Label>` suffix, `(feat. @handle)` → `feat. Handle`.
   A dash only separates artist and title when what follows it is one: if the right side is
   *only* a video label (`"Mad World" (feat. Gary Jules) - Official Music Video`), the whole
   text is the song and the artist comes from the channel (added 2026-09-23; v2 handled this
   shape with ~40 literal suffix strings, which is exactly the tuning this rule replaces).
4b. **A playlist on the artist's own channel is not a compilation of that channel** (2026-09-23).
   Its video titles carry the album around — "Feuerschwanz Methämmer - Song by Song - …",
   "Das Elfte Gebot - Unboxing" — which counted as two more artists, so `classify` said
   compilation and the *channel handle* became the album artist (`xxFEUERSCHWANZxx`).
   `named_artist()` is what classification and the album artist now count: the artist a title
   names, with the guest credit removed (§5.6) and a trailing copy of the playlist title
   stripped; an entry that names none (only the channel is left) counts for nothing.
   Fixtures `artist_channel_playlist{,2}.json`.
5. Label / lyrics / fan channels (Napalm Records, "Common Sense", "dernachtwaechter")
   are never the artist. Reverse order ("Lullaby of Woe - Ashley Serena") is only fixable
   by a lookup (MB recording search both ways) or the user.
5b. **When the uploader stands in as the artist, the title usually still names the real one**
   (2026-09-23, found by auditing the library for `artist == channel`). Rules, each general and
   each from a real case: a credit at the end ("No Sound But The Wind **by The Editors**") is
   read only when nothing else names an artist — a song may simply contain the word ("Killed by
   Death"); a title that repeats our own artist loses it ("Metallica: Nothing Else Matters");
   `'single quotes'` delimit a title like double ones ("NEBELUNG 'Mittwinter'"); a colon
   separates; a dash separates without a leading space only when what follows starts a name
   ("Arcana- Innocent Child" splits, the German compound "sang- und klanglos" does not); and
   invisible bidi/zero-width marks are stripped before anything else ("In The Nursery ‎– …",
   whose U+200E hid the separator).
5c. **Video facts are not audio facts** (2026-09-23). "(1080p)", "HD 1080p", "Full HD" and
   the label words in other languages ("Oficial") describe the upload and are dropped —
   "(Remaster)", "(2012 Remaster)", "(Remastered 2023)" describe the recording and stay, so
   "(Remastered 1080p)" keeps its audio half. A trailing run of noise words is judged as a
   whole and only dropped if one of them labels a video: "Full HD" goes, "Life Is Full" stays.
5d. **A publisher suffix after a slash goes** (2026-09-23): "U-Gra (Tagelharpa playthrough) /
   Napalm Records". A slash is not a separator like "|" — real titles contain one ("Intro /
   Outro", "AC/DC", "24/7") — so the segment must name a publisher (Records, Recordings,
   Entertainment, Productions, …) before it is cut. Checked against the library:
   "Hexentanz / Henkersmahlzeit / Gebt Acht!" and "Auschwitz / Birkenau" stay whole.
5e. **An album name repeated in every track is a label** (2026-09-24). YouTube Music writes
   "1 - Der Kuss des Kometen (Teil 01)" for all 31 parts of an audio play. Judged per album,
   never per title: the prefix must appear on at least three tracks and 80% of them, so
   "Carolus Rex (Swedish version)" among fifteen unrelated titles keeps its name — stripping
   that one would have left "Swedish version". A single-word album never strips, so a title
   track survives, and nothing is cut when the remainder would be empty.
6. **Guest credits live in the title, never in the artist field** (2026-09-23):
   `Feuerschwanz ft. Melissa Bonny` / `Ding` → `Feuerschwanz` / `Ding ft. Melissa Bonny`,
   applied to all three sources (video title, YouTube Music, MusicBrainz artist-credit).
   Otherwise every collaboration becomes its own "artist" with its own folder. The marker is
   kept as written, a guest already named in the title is not repeated, and a credit naming
   the track's own artist is dropped (`Gary Jules` / `Mad World (feat. Gary Jules)`).

Every rule gets a fixture case from `design-fixtures/vol1.json`; the expected results are
the right-hand column of the table in §8.

## 6. Data model (one shape, versioned)

```
Collection  { source_url, source_id, kind, title, channel, fetched_at, entries[] }
Entry       { video_id, position, title, channel, duration, chapters[], music{artist,track,album,year}, status: ok|skipped(reason) }
Field<T>    { value: T, provenance: mb|yt_music|yt_title|playlist|user }
AlbumPlan   { schema: 1, album, albumartist, year, cover, folder, tracks[] }
PlanTrack   { video_id, number, disc, artist, title, filename, state: pending|done|failed(reason) }
```

- "Entries in the playlist" and "songs on the album" are different numbers and are
  never stored in the same field.
- Counters shown in any UI are **computed** from these lists, never maintained by hand.
- Schema changes bump `schema` and come with a migration of `.ytalbum.json`; there is
  never more than one live shape.

## 7. Tech choices

- Python 3.14, `uv`-managed venv, `pyproject.toml` with pinned deps
  (`yt-dlp`, `mutagen`, `httpx`; nothing else until needed).
- yt-dlp **Python API** (`YoutubeDL.extract_info`) in a thread pool with a semaphore —
  not dozens of CLI subprocesses. Timeouts on everything.
- No cookies by default. Optional exported `cookies.txt`; never `--cookies-from-browser`
  on every call (v2 decrypted Chrome's DB ~50× per search).
- MusicBrainz: real UA with contact, one async-safe limiter (≤1 req/s, lock + monotonic
  clock), retry on 503, Lucene escaping; disk cache (sqlite) for hits; misses cached
  short (1 h), errors not cached.
- ffmpeg only for remux (`-c:a copy`) and chapter splitting.
- Config: one TOML file (`~/.config/ytalbum/config.toml`), overridable per run by CLI
  flags. Holds `library_root` (**configurable, no default path baked into code**; the
  first run asks, or takes `--library`), filename template, compilation naming rules,
  MB on/off, concurrency, JS runtime.
- UI: **CLI first** (`ytalbum fetch <url> [--dry-run] [--edit]`). Web/PWA later on top
  of the same library — the library never knows about the UI.

## 8. Test cases (all offline from saved fixtures, plus one opt-in live smoke test)

| Fixture | Asserts |
|---|---|
| `tab_playlists.json` (@MyDarkLullabies/playlists) | channel → 20 sources; flat `playlist_count` is never used as a track count |
| `vol1.json` (Vol. 1 - Heavy Sleeping) | kind = compilation; intro card skipped → 13 tracks; parsed artists: Enemy Inside, Dominum (the `feat.` belongs in the title, §5.6), Mono Inc(.), Schandmaul, Subway to Sally, Lacrimosa, Letzte Instanz, Mantus, Erben der Schöpfung, Disturbed, Ashley Serena*, Lord of the Lost, Tungsten (*needs lookup) |
| `legends.json` (Sabaton channel "Full Album" playlist) | kind = artist_playlist, not official_album; MB release "Legends" must NOT be auto-accepted (17 entries vs 11 songs) |
| to capture: an `OLAK5uy_` album | kind = official_album; MB match accepted; tracklist from MB |
| to capture: a chaptered full-album video | chapter split, titles from chapters |
| to capture: Carolus Rex | release-country preference picks the international edition |
| to capture: albums titled "1984" / "1918" | normalisation never strips the year-like title |

## 9. Vertical slices (each ends with something usable)

1. ✅ **Paste playlist URL → tagged album folder** (any kind, metadata from YouTube only),
   plus `--dry-run` plan output. Includes JS-runtime setup (§3.5). *Done 2026-09-22:*
   Vol. 1 downloads 13/13 (Opus 251 copied, 147 kbps), tags + 1280×720 cover embedded,
   resume and per-track retry (YouTube sporadically answers 403) verified live.
   Known gaps, left for their slices: raw video titles for 8 of 13 Vol. 1 tracks
   (slice 2), YouTube covers are 16:9 not square (fixed later: `cover.py` crops
   pillarboxed thumbnails to the square art — only when everything cut away is uniform
   background, centred unless content forces a shift, never user covers), a single
   video is filed as a 1-track "album" under its YT Music album name.
2. ✅ Compilation parsing (§5) + intro skipping; Vol. 1 comes out right. *Done 2026-09-22:*
   11 of 13 Vol. 1 tracks exact; the two left (reversed "Song - Artist" on a lyrics
   channel, `@xxHANDLExx` feat. credit) are by design for the lookup in slice 5.
   Also: `release_year` exists on plain videos too (upload year) — only trusted together
   with an `album` field.
3. ✅ Incremental re-run from `.ytalbum.json` (Vol. 20 grows → only new tracks). *Done
   2026-09-22:* the plan records the auto-derived value of every field; a differing
   value is a user edit and always wins, untouched fields follow better derivations.
   `folder`/`filename` in the plan mean *what is on disk*; wanted names are computed and
   the executor renames/moves (never overwriting). Tags carry a signature → retag only
   on change (e.g. tracktotal grows). `cover.*` in the album folder is used (user can
   replace it). Albums are found by source id, so renamed folders are still updated.
   Verified live: re-running Vol. 1 renamed 8 files to their slice-2 names, retagged
   13, downloaded 0. **Corrected later:** "existing numbers stay, new tracks are appended"
   was wrong for curated playlists — Feuerschwanz (12th in Vol. 20, readable only later)
   became track 13. Numbers now always follow the source order (MusicBrainz' numbering
   for matched releases); tracks gone from the source go last. Re-sorting the playlist on
   YouTube → the next fetch renames and retags, downloads nothing.
4. ✅ Channel URL → pick which collections to fetch. *Done 2026-09-22:* Releases tab
   (official `OLAK5uy_` albums + singles) listed before Playlists; `--pick`/`--all` or
   an interactive prompt; "✓ in library" markers; `ytalbum update` re-checks every album.
   Found on the way (Vol. 20): age-restricted videos → opt-in cookies
   (`config --cookies-from-browser/--cookies-file`), skipped until then and picked up by
   a later `update`; YouTube titles can be Unicode-decomposed (NFD) → normalised to NFC
   at the mapping boundary; German/360°/unbracketed video labels; partial labels keep
   their meaning ("(Official Live Video)" → "(Live)"), and brackets are only treated as
   labels if they contain a marker word ("(Music of the Night)" stays).
5. ✅ MusicBrainz enrichment (album + recording level) with provenance and cover art.
   *Done 2026-09-22:* Vol. 1 13/13 recordings (incl. the reversed "Lullaby of Woe" via a
   swapped query; the credit "DOMINUM feat. Feuerschwanz" picked because the
   `@xxFEUERSCHWANZxx` handle contains the guest's name — since 2026-09-23 stored as
   artist "DOMINUM" + title "The Dead Don't Die feat. Feuerschwanz", §5.6), Vol. 20 9/12, official Legends matched as a release
   (year, tracklist, 500×500 Cover Art Archive front, `musicbrainz_*id` tags). The fan
   "Full Album" playlist is correctly *not* accepted as the release. Rules found on the
   way: a title with extra info MB lacks ("(Live)", "(Behind The Scenes Documentary)")
   confirms the artist but gets no recording id; MB answers 503 even to the first
   request, so retry/backoff is mandatory; our own saved cover is upgraded when a better
   source appears, a user's cover never is. Cache: `~/.cache/ytalbum/musicbrainz.sqlite3`.
5b. **Third lookup strategy: split the title** (2026-09-23). When the uploader stood in as the
   artist (`artist == channel`) and the normal and swapped queries found nothing, the real
   artist is often glued to the title with no punctuation ("Assemblage 23 Lullaby",
   "Joachim Witt Gloria"). Every split is tried and `pick_recording` must confirm *both*
   halves, so a query for the right words cannot return a wrong band. Unbracketed trailing
   video labels are dropped first ("Gloria Offizielles Musikvideo"), else the title half
   never matches.
6. ✅ Artist search (channel releases/playlists tab, Topic channel, YT playlist search).
   *Done 2026-09-22:* `ytalbum search "Faun"` → YouTube Music album search → dominant
   uploading channel whose name contains the artist → its Releases tab; grouped as Albums
   (MusicBrainz studio albums or ≥5 tracks) / Singles, EPs / channel playlists / other
   playlists; duplicates of one album merged; MB albums not found are listed. Curators
   (no YT Music albums) are found through their own playlists. Verified live: Faun →
   13 albums, HEX downloaded with MB release match (guest credits) in one command.
   Also fixed here: a new album's folder follows the enriched names; a merge never
   replaces a value by a less trusted one (user > MB > YT Music > title/playlist), so an
   `update --no-mb` or an MB outage cannot undo MusicBrainz corrections.
7. ✅ Web UI / PWA on the library. *Done 2026-09-22:* `ytalbum serve` — stdlib HTTP
   server + plain HTML/JS (no build step, no WebSocket state machine); library grid with
   covers, album view with editable fields and provenance badges (edits = USER values,
   then rename/retag on disk), one input for URL-or-artist (preview / pick from search
   or channel), "Update library", job cards with logs. Jobs run one at a time in a
   single worker. Orchestration moved to `service.py`, shared by CLI and web. Safety:
   localhost by default, writes need an `X-Ytalbum` header (no cross-site POSTs), Host
   check (DNS rebinding), covers only by album id, strict CSP, all YouTube text rendered
   as text. Installable as an app when opened via localhost (service workers need a
   secure context; over plain http on the LAN it works as a web page only).
   Verified in a browser: grid, album view, update job ending "blocked" cleanly.
   Later: theme switch (auto/dark/light, remembered per browser); the page references
   `app.js`/`style.css` by content hash — a one-hour cache had kept the old script (no
   browser dropdown) alive after an update.
8. ✅ Intro/outro trimming — **manual, after automatic detection was measured and dropped**
   (2026-09-22). Measured on the owner's library: 0 of 39 videos have chapters; two
   Napalm Records uploads share no detectable opening (loudness correlation +0.46, while
   two unrelated Sabaton tracks reach +0.69) and no common silence structure; MusicBrainz
   lengths show *that* there is extra material (30 of 66 tracks longer, up to +234 s) but
   never where, and the big ones are cinematic music-video intros where a cut would hit
   the song. So: trim points per track in the plan (`trim_start`/`trim_end`), cut
   losslessly with `ffmpeg -c copy` from `.originals/<video_id>.opus`, which is kept, so
   clearing the trim restores the original byte for byte; one button applies a trim to
   every track of one uploader across the library.

9. ✅ Library hygiene and desktop integration (2026-09-23). Artist fields hold the performer
   only — yt-dlp lists writers and producers in `artists` too, MusicBrainz credit phrases
   carry guests, and both had produced folders like `Feuerschwanz feat. Melissa Bonny`.
   Added: performer-only extraction, guest credits into the title (§5.6), one spelling per
   artist across the library, and a video listed twice in a playlist counted as one track.
   `ytalbum repair` applies all of it offline to what is already on disk (21 tracks in 12
   albums here), and skips anything the user edited.
   Also `ytalbum app install`: a browser-installed PWA keeps the browser's window class
   (`WM_CLASS = "crx_<app-id>", "Google-chrome"`), and desktops group the taskbar by that
   class, so it shows up as another browser window. No manifest key changes it — the class
   comes from the browser process, and `--class` is only honoured by a process of its own,
   so the launcher pairs it with a profile directory of its own.

10. ✅ Finding things in the library (2026-09-23). The grid sorts artist → year → name, so a
   discography reads chronologically while compilations, which have no year, keep their
   natural order. A filter over album, artist **and song** titles: `/api/tracks` serves every
   track as compact rows (video id, artist, title, downloaded, trim points) with a version
   taken from the plan files' mtimes, so the page fetches it once and again only when an
   album changes — 95 KB and 11 ms for 1338 tracks, and typing costs no request. Matching
   folds case, accents and punctuation, plus what NFD cannot: ð/þ/ø/æ never decompose
   ("njord" → *Njǫrð*), and a German keyboard writes "knueppel" for *Knüppel*. Folding records
   where each folded character came from, so the match is highlighted in the original text.
   An input's value cannot be highlighted character by character (no CSS, no Custom Highlight
   API, and mirroring text behind a transparent input breaks on scroll, fonts and IME), so a
   matching field in the album editor is tinted whole. **▶ Play** fills the existing queue
   from the filter — the matching songs of each album, or all of an album that matched by
   name. Deliberately not built: shuffle, repeat, reordering, persistence. The player is here
   to check downloads, not to replace a music player.

11. ✅ "No audio-only stream" is not a property of the video (2026-09-23, from real use).
   DOMINUM "One of Us" failed with it and a plain re-fetch got the full-quality Opus
   (`ext=opus`, `audio_choice=best`); Skeeter Davis' 1963 upload fails every time and really
   offers only *360p video, AAC*. Identical symptom, opposite cause — and the old dialog
   pushed the user toward an `.m4a` that is audibly worse than a file that was available.
   So the download asks a second time (after making sure the token server is up) before
   raising `NoAudioStream`, and the dialog now says the failure can be temporary and to
   re-check the source first. Only a video that refuses twice is treated as having no audio.

12. ✅ Two things real use turned up on 2026-09-24.
   **An album of unusable videos is not an album.** Heavysaurus' eight *Folge* Hörspiele are
   YouTube Music Premium exclusives: all ~31 entries per album answer "only available to
   Music Premium members" (confirmed with the owner's own cookies). That reason is permanent,
   so the §3.8 guard — which holds a run back when entries fail *temporarily* — let them
   through one by one, and a plan with zero tracks was written: eight folders under "Unknown
   Artist", cover art and nothing else. A source whose every video is unusable now reports and
   writes nothing.
   **A disc split survives an update.** `merge_plans` took `disc` from the fresh plan, and a
   YouTube playlist is always flat, so splitting an album into media by hand would be undone
   by the next update. A flat source now says nothing about media; only a fresh plan that has
   discs of its own (a matched release) may change them, and a video that appears later joins
   the last disc.

## 10. Rules for whoever implements this (lessons from the v2 loop)

- **Fix wrong data where it enters,** not where it shows up. If a number is wrong on a
  card, trace it to the extractor before touching dedup/merge/display code.
- **Every bug gets a fixture test first** (captured JSON, offline), then the fix.
  No ad-hoc `debug_*.py` / `test_*.py` in the repo root, no live-only "tests",
  no inspecting a running server by constructing a second instance in another process.
- **Never tune for one artist.** Two failures in a row on the same symptom → stop and
  re-examine the assumption, don't add a third layer.
- Commit everything that runs (v2's whole frontend was never committed).
- No hardcoded `/home/tordt/…` paths or ports scattered across files; one config.

## 11. Salvage from v1/v2 (as reference, rewritten)

- Filename convention and tag rules (albumartist vs artist, disc only if >1) — v1
  `downloader.py:1282`, `audio_tagger.py:186-244`.
- Per-track tag-on-arrival (v1 `downloader.py:748-785`).
- Release-country priority table (v2 `models.py:120-135`); MB track count = sum of
  `media[].track-count` (v2 `musicbrainz_service.py:316`).
- YouTube id extraction (v2 `youtube_deduplicator.py:22-67`), extended for
  `music.youtube.com` and `OLAK5uy_`.
- Cover Art Archive lookup (v2 `musicbrainz_service.py:358-419`).
- Title-suffix lists from v2 `normalization_service.py`, **without** the year regex.
- Negative keywords for search ranking (karaoke, cover, reaction, …; v1
  `advanced_search.py:678`).

## 12. Decisions (2026-09-22)

- Compilation albumartist = curator; album = playlist title minus curator prefix (§2.1).
- Library root is configurable (§7).
- v3 lives on a **new branch** in this repo, which became `main` (`github.com/smtws/ytalbum`).
- Intro/outro trimming: yes, later — after the rest is stable (slice 8).
