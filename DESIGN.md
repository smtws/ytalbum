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
5e. **An album name repeated in every track is a label** (2026-09-24). The shop writes it
   wherever it likes: "1 - Der Kuss des Kometen (Teil 01)", "Kapitel 01: Die Hexenmeister des
   Metal (Folge 4)", "Louder Than Hell (Live in Hamburg)". It is removed wherever it sits,
   together with a bracket group that only repeats the release and the empty pair left behind.
   Judged per album, never per title: at least three tracks and 80% of them must carry it, so
   "Carolus Rex (Swedish version)" among fifteen unrelated titles keeps its name — stripping
   that one would have left "Swedish version". A single-word album never strips, and nothing
   is cut when the remainder would be empty.
   Three bugs this found, each with a test: `casefold()` maps "ß" to "ss", so a pattern built
   from the album silently missed "Auf großer Tour"; a bare "4" from "Folge 4" matched inside
   "Kapitel 04" without word boundaries; and MusicBrainz enrichment re-adds bracket groups it
   does not know, so the judgement is made again after enrichment, not only in `build_plan`.
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
- Lyrics: **LRCLIB** (`lrclib.net`, no key, community-contributed), same client shape as
  MusicBrainz — UA with the repo URL, ≤1 req/s, retry on 503 (it answers "server is busy"
  readily), sqlite cache (hits 30 d, misses 7 d). Measured 2026-09-25 on 40 random library
  tracks: 50 % synced, 22 % plain, 28 % nothing. The text is third-party and unlicensed;
  ytalbum only puts it beside a file the user already has, and `--no-lyrics` / `config
  --lyrics off` switches it off.
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

13. ✅ One spelling per artist, chosen by evidence (2026-09-24). The library had "SALTATIO
   MORTIS" beside "Saltatio Mortis" and three spellings of Lord of the Lost, for three
   reasons, each fixed: the album artist was unified *after* `relocate` had chosen the folder,
   so a renamed album stayed put; `_harmonize_artist` renamed without refreshing the derived
   paths, so a new album kept the folder of the spelling it had just dropped; and `repair`
   compared names only, skipping albums whose plan was already right but whose folder was not.
   The spelling itself is now chosen by evidence rather than by alphabet: one the user typed
   wins, then one MusicBrainz confirmed, then mixed case over a shouting channel name. Before
   that, "Lord Of The Lost" beat "Lord of the Lost" because "O" sorts before "o".

14. ✅ The track order can belong to the user (2026-09-24). A YouTube playlist's sequence is
   often just the order things were added in — the owner of My Dark Lullabies keeps the
   canonical order on Spotify, and resorting 15 volumes by hand would have been undone by the
   next update, because `merge_plans` renumbers from the source. Editing a position now sets
   `provenance["order"] = user` on the album; a merge then keeps the user's numbers and puts a
   video that appeared since at the end. The position is an input in the album view, and the
   play button moved into a cell of its own so showing it on hover no longer shifts the row.

15. ✅ Lyrics belong to the file, not to the tagger (2026-09-25). Three measurements decided
   the shape. LRCLIB's exact endpoint (`/api/get`) needs *its* album name and the length
   within ±2 s, so for compilations only the `/api/search` path can work — and a search on
   artist + title alone is what attaches a cover version's words to the original, so a
   candidate is refused unless its length is within 3 s of the **file's own** (mutagen, after
   trimming; YouTube's duration includes intros). `tag_file` replaces every tag on every pass,
   so the `LYRICS` comment (`©lyr` on m4a) is written *from* a `.lrc` sidecar rather than
   preserved: the sidecar is the truth, delete it and the tag goes too. That also serves the
   player that matters here — MPD/Volumio has no lyrics tag at all and reads `.lrc`. The text
   is not kept in `.ytalbum.json` (3946 × ~3 KB would land in every `update` and in the track
   index, which is 11 ms today); the plan holds only `lyrics: synced|plain|instrumental|none`
   and the lrclib id, so nothing is ever looked up twice. Two things about lrclib's
   `instrumental` flag, learned from real use: it means *nobody submitted words*, not that the
   recording has none (16 of 18 entries for one Feuerschwanz song are such stubs), so an entry
   without words never ends the search; and when our own title says "(instrumental)" the sung
   version's words are refused however well the lengths agree, because an instrumental cut is
   exactly as long as the sung one and only the title can tell them apart. The marker is read
   from the **track** title alone: an album called "(Instrumental)" claims something about
   every track on it, and such records do turn up with a sung intro or outro, while a track
   title is written per recording. It costs nothing here either — every instrumental track in
   the library carries the word itself, so the album name would reach none of them. A trim changes the file's length, so the
   track is matched again afterwards — but its timestamps are never moved: the match was
   gated on *this* file's length, so the recording that matched is the audio in front of us.
   (Shifting them by the trim, as the first version did, moved them a second time.) Two things the
   first run over the real library taught: lrclib's exact endpoint refuses a duration over
   3600 s (a 63-minute ambient piece got HTTP 400 on every run), so that request is not made
   at all, and any 4xx is now remembered as "nothing here" — only 5xx and network errors are
   worth asking again. Coverage over 3946 tracks: 52 % synced, 15 % plain, 4 % instrumental,
   29 % nothing; audited against lrclib on 200 matches, none had the wrong artist or song.

16. ✅ A credit's typography is not the artist's name (2026-09-25). "Visions of Atlantis"
   stood in the library twice, and both spellings honestly carried `mb`: MusicBrainz credits
   releases as they are printed, and three of seven credit "Visions **Of** Atlantis" while the
   artist entity is always lower-case. With nothing left to weigh, harmonisation fell through
   to its last resort, the alphabet, where "Of" beats "of". A credit now yields the artist
   entity's own spelling whenever the two differ only in case or punctuation — `key(credit) ==
   key(entity)`, the same equivalence class every other inference step works in, so a credit
   that names something *else* ("Puff Daddy" for the artist "Diddy") is a deliberate editorial
   decision and is kept. It costs no requests: `inc=artist-credits` already carries
   `artist-credit[].artist.name` beside the credited name, at release **and** track level.
   Measured over the 175 releases in the cache: 168 credits identical, 5 restyled, 0 named
   differently — and MusicBrainz is not quietly de-stylising bands, since its canonical name
   for DOMINUM is "DOMINUM". Nothing in the library changes on a later update (the five were
   already repaired by hand), and the two albums where the user chose "UNIVERSUM25" over
   MusicBrainz' "Universum25" keep that choice, as `user` outranks everything.

17. ✅ The player hears the file the trim describes (2026-09-25). Clicking a lyric line to
   jump there only works if the clock in the page is the clock in the audio, and it was not:
   trim points count from the start of the *video*, but `/api/audio` served the file already
   cut to them, so the player cut the head a second time. Measured on the one trimmed track
   in the library: playback started 8.16 s into an already-trimmed file, and the first eight
   seconds of the song could not be reached at all. A track whose file was cut is now played
   from `.originals/` (`/api/audio?o=1`, the file `trim.apply` keeps anyway) and the player
   previews the trim itself, which puts the trim handles, the text fields, the lyric
   timestamps and the audio on one clock again. The line being sung is then marked as the
   song plays — the box scrolls itself, never the page, because `scrollIntoView` would take
   the editor with it (slice 12 learned that the hard way).

18. ✅ Three opinions on how long a song is (2026-09-25). MusicBrainz knows the recording,
   lrclib answers even when its recording was too far off to take the words from, and the file
   on disk is measured when it is tagged (`file_length`) — so a disagreement is visible without
   opening anything. The thresholds come from the library, not from taste: 13 % of 3032
   comparable tracks are more than 5 s longer than MusicBrainz, so marking *any* mismatch would
   mark 138 of 245 albums. The track shows the signed gap (amber past 20 s, red below 60 % of
   the known length — that is not the song), and an album is flagged only when **half** its
   comparable tracks are off by more than 20 s the same way: 11 albums of 245, a worklist
   rather than wallpaper. The first one it caught was "Sabaton — Heroes", eleven
   track-commentary clips of 30–50 s filed as the album, because YouTube's own title
   ("Heroes (Track Commentary Version)") lost its bracket group to `core()` when the release
   was matched and MusicBrainz then named it "Heroes". Replaced by the real album, where
   every track now lands within 3 s. Two thresholds were corrected once the library answered:
   a *stub* (a file under 60 % of the known length) flags an album on its own, because only 8
   of 3946 tracks are one and every one was a snippet, a radio edit or a wrong recording;
   while a whole-album drift needs **three** tracks, not two — at two, a volume where only
   four tracks can be compared at all was flagged for a pair of long folk songs. A rule that ignored
   lengths repeating across an album (23 of Judas' 56 tracks read 222.1 s) was **built and
   then reverted the same day**: the audio release turned out to have 24 tracks of exactly
   223 s, because those collaborations are one arrangement sung by different guests. The
   repetition was real data, and suppressing it hid a true flag — that album's files were
   official videos running ~50 s long. Repetition is not evidence of a bad reference.

19. ✅ A bracket group can say the recordings are different ones (2026-09-25). `core()` strips
   bracket groups before comparing titles, which is right for "(Deluxe Edition)" and wrong for
   "(Instrumental)": the first names the same recordings, the second does not. Measured on 35
   library albums against their real YouTube titles: 5 had an edition marker MusicBrainz
   lacked (correctly dropped), 1 a version marker — "OPVS NOIR Vol. 1 (Instrumental)", filed
   as the ordinary album. A release candidate is now refused unless its version markers match
   ours; re-fetched, that album did not merely keep its name, it matched the *right*
   MusicBrainz release. The same measurement found the companion fault: a track whose
   recording was refused ("(Live)", a cover) still kept that recording's length, so 55 tracks
   carried a length they never had and 21 of them were marked by the new chip — a live cut
   against its studio version reads as two minutes off. `mb_length` is now only written when
   the recording is accepted, and `repair` gives up the ones already stored.

20. ✅ A trim keeps the track's own format, and a failure says so (2026-09-26, from the QA
   run). The kept original was named `<video id>.opus` whatever the track was, and the cut was
   always written into `.trim.opus` with `-c copy`. A track trimmed as opus and then switched
   to the combined stream was therefore re-cut from the *previous format's* original: ffmpeg
   copied Opus into a file named `.m4a`, the tagger called `MP4()` on it and raised, and that
   exception aborted every later run over the album until the file was deleted by hand — while
   the plan still read `state=done, trimmed=None, error=None`. Now the original carries the
   track's extension, the cut keeps the track's container, and a kept original that is not what
   its name says is never cut from: if the file on disk is still the untouched download a fresh
   original is taken from it, otherwise the trim is refused with the recovery path named. No
   original is ever invented from an already-cut file. Two further faults came out of the same
   case: a trim that fails **was never recorded** — `run()` set `track.error` and then saved
   nothing, so with ffmpeg missing the plan advertised a trim that never happened and the web UI
   showed nothing at all — and a file that cannot be tagged now fails **its own track** instead
   of the album's run. Prune also gives up the kept original, as `delete_track` always did.
   Two mirrors of the same rule came out of the review: **clearing** a trim with nothing to
   restore from is refused as well, because a plan that calls a cut file untouched is the same
   lie in the other direction; and a re-downloaded track gives up its `trimmed` signature,
   since a fresh file is untouched whatever the plan said before — otherwise the trim points
   sit there matching a signature that describes a file that no longer exists, and are never
   applied again.

21. ✅ Lyrics beside a track belong to whoever wrote them (2026-09-26, from the QA run).
   "Lyrics you write yourself are never touched" held only if you had also set the flag that
   said so: editing a `.lrc` by hand and re-running `ytalbum lyrics --refetch` overwrote it, and
   deleting one left the plan claiming `synced` while no file was there. Both because the plan
   knew the *status* of a lyric but nothing about the file. It now records the bytes it wrote
   (`lyrics_sha`, the twin of the cover's `sha1`), and every pass reconciles the plan with the
   disk before anything is looked up: a sidecar whose hash has changed is yours from then on,
   a sidecar that is gone sets the status to `none` and drops the tag, and a sidecar with no
   lrclib id behind it was never ours to begin with. The interesting case is the library that
   predates the record. The obvious test — does the file still say what the tag says? — sounds
   decisive and is not: every pass writes the tag **from** the sidecar, so an edit made before
   the last pass reads back as perfect agreement, and that is the *common* case, not the rare
   one. So a difference to the tag is taken as proof of an edit, agreement proves nothing, and
   the question is left open until something actually wants to overwrite the file — at which
   point lrclib is asked what the entry we saved holds today. Equal means ours (the hash is
   recorded and it is never asked again), different means yours, and no answer at all means the
   file is kept and the question stays open. The check costs nothing for a track looked up
   recently, because the cached search bodies already carry whole rows. Two smaller holes went
   with it: `--refetch` used to clear the `lyrics_id` it needs to ask that question, and
   `ytalbum lyrics` skipped albums with nothing to look up — so on that path a deleted sidecar
   was never noticed. A trim, finally, clears the status to force a re-match; for a lyric of
   yours it now restores the status from the words on disk instead of leaving the track blank.

22. ✅ The arrangement is the order the tracks are in, not the numbers (2026-09-26, from the QA
   run). Two faults with one cause. Prune closed the numbering gap on a single-disc album and
   left it on a multi-disc one — `1, 2, 4 …` in the tags — because the decision was made by
   `if all(t.disc == 1)`, not by any rule about order. And collapsing a disc split back to one
   disc reshuffled the album: the numbers of a split are per disc, so `1-01…1-03 / 2-01…2-03`
   sorted by `(disc, number)` interleaved into 1, 1, 2, 2, 3, 3. A split was reversible on paper
   but not in arrangement. The fix names what the arrangement actually is: the order the tracks
   stand in, which is the order the album view shows and the order the browser posts back.
   `renumber_discs` is therefore `arrange`, and it no longer sorts at all — it groups by disc and
   counts each disc from 1. Where a sort *is* wanted, because the user typed numbers, it happens
   first and against the disc each track **was** on, where the numbers are unique; a number just
   typed also beats the same number left standing on another track, so typing 1 on 2-04 makes it
   lead and moves 2-01 down. Prune then closes the gap on every album, per disc, and keeps
   `provenance["order"] = user`: that flag protects the sequence from the *source* renumbering it,
   and a deletion the user asked for is not the source — `delete_track` has always renumbered.
   The sort had to go entirely in the end, because a number the user types is a *position* and
   sorting cannot deliver one: a track moved down still sorts ahead of whatever holds the place
   below its target, so typing 5 on the first of five tracks put it fourth, and no typed number
   could move a track to the end at all — sorting can place a track before the one whose number
   it typed, never after it. The upward move worked, which is why the asymmetry stayed hidden.
   So the typed tracks are lifted out of the arrangement and put back at the index they asked
   for, lowest number first, while the untouched ones keep their relative order (`placed`). Two
   typed positions in one save, one up and one down, both land where they were asked to.

23. ✅ A fetch renames only the album it is fetching (2026-09-26, from the QA run). Unifying the
   spelling of an artist looked done — `SCHANDMAUL` and `Schandmaul` are one folder — but only
   when the newcomer was the worse-spelled one. Arriving with a *better* spelling, it simply kept
   it: the library's other albums stayed as they were and the artist had two folders (E7). The
   obvious fix is the wrong one. A fetch of one single must not rename twenty other albums as a
   side effect; that is `ytalbum repair`'s job, run deliberately. So the incoming album adopts the
   spelling the library already holds, even when its own evidence is better, and when it *is* the
   better evidence one line names both spellings and says `repair` unifies them. A spelling the
   user chose for the album being fetched still wins for that album — the one case that leaves a
   second folder, and it is theirs to make. The trade-off, accepted: an older spelling can stand
   until repair runs.
   Two pieces make that promise keepable. First, an album is made consistent with *itself* before
   the library is consulted: MusicBrainz credits the release and the tracks in separate fields and
   they disagree (`LORD OF THE LOST` on the release, `Lord of the Lost` on every track), so when
   the most common track artist is key-equal to the album artist, spelled differently, and carries
   MB provenance, the album adopts the tracks' spelling. Second, `repair` does the same step — and
   it has to, or the hint would be a lie: the library adoption overwrites the album-level spelling,
   so after the fetch the better evidence exists **only** in the track credits, where repair's
   album-level comparison would never have seen it. Measured read-only over the 246-album library
   before shipping: 0 albums would be renamed by that step today, and the 3 whose tracks disagree
   with their album artist are all albums the user spelled themselves, which the first guard
   protects. A dry run runs the whole harmonisation too, so the preview is the outcome — it used
   to print the pre-harmonisation spelling and differ from what the fetch then wrote.
   Two details cost a verification round each. An adopted spelling must carry the evidence *it*
   has, not the evidence the album had: adopting while the album's own name came from MusicBrainz
   left the `mb` marker on a shouted name, and repair then converged on the shouting, confidently.
   And repair's older rule ("use the most common track artist when the album artist came from
   YouTube") renames without leaving a marker, so the tracks' spelling lost a tie to another
   mixed-case spelling in the library — alphabetically, which is a coin flip. Both fixed by naming
   the evidence at the moment the spelling is taken; `user` is never inherited either, since it
   would freeze the album against later harmonisation.
   Repair also had to stop asking the question once per album. Deciding against the library *as
   stored* meant an album already visited could not learn from evidence found later, so three
   albums in three spellings took two passes to settle — "run repair" could mean "run it twice".
   One scan now collects every candidate for an artist key (each album-level spelling with its
   provenance, plus `track_spelling`'s answer where its guards hold), the key is **decided once**,
   and every album of that key that is not the user's adopts it in the same pass. Same fixed point,
   one scan instead of one per album, and the pass after it has nothing to do. A tie between two
   equally common track spellings is settled by evidence and then by `spelling_rank`, because
   `max(set(names), key=names.count)` settled it by set iteration order — which hash randomisation
   makes differ between runs. On the real 246-album library repair changes nothing at all, verified
   by copying every plan into a scratch tree and running the real `repair` over it.

24. ✅ What lrclib says a song is long is a consensus, not a nearest miss (2026-09-26, from the
   QA run). When no candidate fits our file, the length kept as the second opinion used to be the
   candidate nearest to *our* length — which for a padded upload is the least representative one
   there is: for the 471 s *Viva Vendetta* video, lrclib's nine entries read 229, 229.8, 229.8,
   230, 230, 230, 230, 230 and 248, and the nearest was the 248. It is now the commonest whole
   second (230), the median of the tied values when nothing repeats more than anything else. The
   question is about the song, so every same-artist candidate answers it together.
   The second half is the query. An instrumental cut has no entry of its own, so asking lrclib for
   "Viva Vendetta (Instrumental)" returned **0 rows** — measured — and the track ended up with no
   length reference at all, although the sung recording's length is exactly the reference it wants.
   The query now drops the NO_VOCALS markers (instrumental, karaoke, backing track) and nothing
   else: a "(Live)" or any other bracket group goes to lrclib as it stands, because a live cut
   really is another recording and studio words must never attach to it. Refusing the *words* for
   an instrumental is unchanged — the marker is still read from the track title, and only wordless
   entries may match — so such a track now gets a length and still gets no lyrics.
   What moves, measured read-only over the real library (stored plans plus the cached lrclib
   bodies, no requests): 16 tracks' lrclib reference changes, most by a second or two and four by
   20-50 s; **no album flag changes** (12 flagged before, 12 after, the same 7 "long" and 5
   "stub"); and 4 per-track chips move, all between nothing and the muted 5 s band. 11 tracks in
   2 albums cannot be computed from the cache, because their searches were made with the marker and
   returned nothing — those are the ones that *gain* a reference, and only a lyrics pass will say
   what it is.
   Accepted, not overlooked: the marker is matched as a bare word too, so a song whose real title
   contains "instrumental" is asked for under a shorter name. The same set already decided whether
   a track's *words* are refused, so the two behaviours stay identical rather than drifting apart —
   which is the property worth keeping here.

25. ✅ A single is one song, so its album name is that song's name (2026-09-26, the user's decision:
   "consistency is more important here than file storage operations", so folder renames are
   accepted). `build_plan` reads a single's album name off the *video* title and enrichment then
   improves the **track** only, so the two drifted apart in the one place a user sees both: S9's
   album read `The Dead Don't Die (feat. @xxFEUERSCHWANZxx)`, the uploader's handle and all, while
   its one track read MusicBrainz' `The Dead Don't Die feat. Feuerschwanz` — and the folder was
   named after the album. After enrichment (and in `repair`, for singles already on disk) the album
   name is set to the track's title, folder and file names following through the existing relocate
   path. Guards: only `kind == SINGLE` with exactly one track, and never an album name the user
   chose. A track title the user chose *is* followed, but the album's provenance is then left as it
   was rather than set to `user` — marking it would freeze the album, so a later edit of the same
   title would stop reaching it. Measured read-only before shipping: the library holds exactly one
   single, whose name is the user's own and already equal to its track title, so `repair` renames
   nothing; the rule is for what arrives next.

26. ✅ The lyrics panel writes as well as reads (2026-09-26, backlog item 1). The whole ownership
   contract of §9.21 is about words a user writes by hand, and the only door to it was the file
   system: find the audio file, name a sidecar with the same stem, write LRC syntax, run a pass.
   The ♪ button now opens a panel that edits, and it appears for a track with no words at all
   (faint) and for one LRCLIB calls instrumental, since those are exactly the tracks whose words
   somebody would want to write. Saving does in one step what `reconcile` does when it *finds* an
   edited file: the sidecar is written as entered, the user mark set, the hash recorded, the status
   derived from the text (`synced` when a line carries a timestamp, else `plain`), the LYRICS tag
   rewritten from the file, the plan saved. **Nothing is looked up** — an editor that asked LRCLIB
   could answer a save by replacing the words just typed. An empty save is a *clear*, not an empty
   file, and it drops the mark with the words, so a later `--refetch` may bring LRCLIB's version
   back exactly as deleting the file by hand does.
   The retag goes through the ordinary pass (`run(..., download=False)`, no lyrics client) rather
   than a second tagging path, so there is one place that writes tags. The price is that a Save is
   not strictly local to one track: the pass walks the album, so a trim left pending on another
   track (one whose `ffmpeg` was missing when it was set, §9.20) is applied then — the same thing
   any other pass would have done, reached from a new direction. The write itself is a job in
   the write lane like every other library change, and jobs now carry the album they hold
   (`Job.target`), so a save is **refused** while a pass is working on that album instead of racing
   it — a pass would retag from the very file the save is about to write. A `fetch` is named by its
   URL and only learns the album id while it runs, so it is not one of the jobs that check can see;
   that is a known gap, not a silent one. A track that is not `done` has no file to put words
   beside and is refused too.
   Nothing about the contract needed a special case for the editor: a sidecar it wrote, then edited
   again on disk, is still the user's, and deleted on disk it follows §9.21 like any other.

27. ✅ One track can be looked up again, and a wrong entry can be rejected for good (2026-09-26,
   backlog item 3). The lyrics button was all-or-nothing: shift-click re-asked LRCLIB for a whole
   album, and the only way to refuse a bad match was to delete the file on disk — which the next
   `--refetch` undid by matching the same wrong entry again. The panel now offers, for words that
   are not the user's, **Look up again** (this track only, with its title, artist and file length as
   they are now) and **Not these words**.
   Rejecting is not "delete": the entry's id goes onto `PlanTrack.lyrics_rejected`, and `Lrclib.get`
   drops rejected ids before anything is judged. So no later lookup can pick it — not a per-track
   one, not a pass, not a `--refetch`. That is the difference that makes the action worth having:
   an entry being the wrong recording stays true however often it is asked for, while a deleted file
   only says "not now". Rejecting immediately takes the **next** best candidate under the unchanged
   rules, or leaves the track at `none` when nothing else fits, so one click ends in an answer
   rather than in an empty panel.
   Rejected ids are dropped from the length consensus too (§9.24), not only from the words: an entry
   that is not this song is no evidence about how long this song is either.
   Neither action is offered for lyrics marked as the user's — those are not LRCLIB's to replace, and
   the editor's Delete is the way to let it answer again (§9.26). Both are write jobs with the album
   in `Job.target`, so they are refused while a pass holds it, and a track with no file is refused.

28. ✅ The fetch preview is the outcome (2026-09-26, the other half of backlog item 2). A preview of
   a URL already existed — `/api/open` has always run `fetch(dry=True)` on the read lane and shown
   the plan — so this slice is about the three ways it was not yet the truth, and about not making
   it compulsory.
   First, **an album already in the library was previewed as if it were new.** The dry branch
   returned before the merge, so the plan shown was a fresh reading of YouTube: it promised names
   that a real fetch would not write, because the fetch merges with the stored plan and keeps every
   value the user has edited. The dry path now merges too (and logs "already in the library as …,
   N new, M no longer there"), which costs nothing — nothing is saved — and makes the preview
   answer the only question worth asking. The panel says which state it is in, and the button reads
   "Download what is missing" rather than "Download".
   Second, the preview showed no sign of tracks that have left the source; those rows are now marked
   *gone*, as the album view marks them.
   Third, **the preview must not become a compulsory click**: Shift+click on Go (or Shift+Enter)
   posts the fetch directly, the same modifier convention as everywhere else in the UI. A form
   submit carries no modifier state, so it is captured on the way in, on the form's capture phase.
   One thing the page itself caught during verification: the in-library line printed the server's
   absolute path, which is no business of a browser. It shows the library-relative folder, and only
   mentions a move when the album would change folders.

29. ✅ A value you overrode can be handed back (2026-09-26, backlog item 5). The plan has always
   kept what the pipeline derived, in `auto`, for every field a user overrides — that is how a merge
   knows which values are theirs — but the UI offered no way back. An edited album artist was frozen
   out of harmonisation and repair with nothing to click, and the only route was editing the JSON.
   The badge that says "you" **is** the way back now: where a field is the user's and something was
   derived for it, the badge is a button that restores the derived value. What it drops matters less
   than what it writes: `_merge_fields` decides a field is the user's by comparing the value with
   `auto` and re-asserts the USER provenance on every merge, so dropping the mark alone would be
   undone by the next update. The provenance is dropped rather than guessed at, because `auto`
   records the derived *value* and never its source; the next pass that touches the field writes a
   truthful marker again. Where nothing was derived (an album from before `auto` was kept) the badge
   stays a badge and says why.
   The order flag resets too, and only that: nothing is renumbered at the moment of the reset, but
   the next update may put the album back in the source's order. The tooltip says so, because a
   button that silently rearranges 56 tracks would be a trap.
   Lyrics are deliberately not in this: the editor's Delete is their way back (§9.26), and two
   affordances for one thing would only be two things to explain.
   Measured read-only over the library this is for: 59 of 246 albums carry at least one overridden
   field — 38 album artists, 20 years, 15 user orders, 3 album names — and 83 tracks (73 artists,
   17 titles). Every one of them has an `auto` value behind it, so every one is resettable.

30. ✅ Opening an album asks the disk (2026-09-26, backlog item 6). Ownership of an edited `.lrc`
   and the status of a deleted one were only noticed when some pass walked the album, so between
   passes a row could show ♪ for words that were no longer there — safe, because `reconcile` runs
   before anything overwrites a file (§9.21), but a user would call it a bug. `/api/album` now runs
   the same `reconcile` for every done track before it answers, so the view is the truth as soon as
   it is drawn; when it found something, one write job saves the plan and brings the tags along,
   with the album in `Job.target` so it queues behind anything already working on it. When plan and
   files agree — the normal case — nothing is written and no job exists, which is the property worth
   testing: three opens of an unchanged album leave every file's mtime untouched.
   The grid is deliberately *not* reconciled: 246 albums would be stat-ed on every render of a page
   that polls. Its counts come from the plans, so they catch up when an album is opened, which is
   the moment a user is looking at that album anyway.
   Cost, measured on the 56-track album (30 of them with lyrics): one `stat` per done track, plus
   reading the sidecar where there is one and the audio's tag where a sidecar has no recorded hash.
   `/api/album` answers in **4–5 ms** against 2–3 ms for `/api/state`, so the check costs about 2 ms
   for 56 tracks; the whole open, measured in the browser, is 22 ms. It is bounded by the album, not
   by the library, which is why the grid is left out of it.

31. ✅ The ⏱ mark leads to a cut (2026-09-26, backlog item 7). The chip said a track was the wrong
   length and left the user with arithmetic: the trim fields are bare seconds, and the only way to
   know whether a cut fixed the length was to save it and look at the chip again. Marking from
   playback already existed (*start here*, *end here*, draggable handles, arrow keys for tenths), so
   this slice is the two things that were missing.
   **A target while trimming.** The trim bar now says what the pending marks would leave, what the
   song is said to be, and the difference — "now 4:45 · keeping 3:38 · MusicBrainz 3:37.6 · +0.4s" —
   recoloured on the chip's own bands as the marks move, so the user watches the gap close instead of
   guessing. The arithmetic lives in `plan.trimmed_gap` and is mirrored in `app.js`'s `trimTarget`;
   the reference is the chip's own (`reference_length`), so there is no second opinion to keep in
   step. It counts from the **video's** duration, never from a file already cut, because that is what
   trim points mean (§9.17) — the test for that case is the one that would catch a future refactor.
   **Which file you are hearing.** A cut track is played from its kept original, or the head would be
   skipped twice (§9.17); the player now says so, and *▶ from start* plays from the start mark, which
   is the question a start mark actually raises ("does the song begin here?").
   Marks are rounded to a tenth. `audio.currentTime` carries a dozen decimals of mouse precision that
   mean nothing musically and end up in the plan and on ffmpeg's command line.
   Deliberately not here: automatic cut detection and a waveform. §9.8 measured detection and dropped
   it, and a chip that leads to a cut does not need to guess the cut.
   The JS half has no unit tests, because this repo has no JavaScript test harness and P14 is not the
   place to introduce one; `plan.trimmed_gap` carries the arithmetic under test, and the browser
   checks in the catalog (section P) are the evidence for the rest.

32. ✅ Rows are dragged, and a typed number counts in the disc you put the track on (2026-09-26,
   backlog item 4, the last of them). Typed positions have landed correctly since §9.22, but typing
   numbers into 56 rows is a poor way to reorder an album. A row now has a grip (`⋮⋮`) in the position
   cell and is dragged with **pointer** events rather than HTML5 drag-and-drop, which does not exist
   on touch; the row moves through the table as the pointer passes other rows, so what is on screen
   is the arrangement that will be saved, and the position column is renumbered per disc on every
   move so it never shows two 3s mid-edit. Escape puts every row back. Alt+↑ / Alt+↓ on a focused row
   does the same move without a mouse. Nothing is saved until the album's save, exactly as a typed
   position is not.
   The grip is deliberate: making the whole row draggable would fight the text selection in the title
   and artist fields, which are the other thing a user does in that table.
   **What this changed on the server, and it is the interesting half.** The client posts every row
   with its number and disc, as the browser already did, so there is no second ordering path — but a
   cross-disc drop did not land where it was dropped. `placed()` grouped every track under the disc
   it *came from*, so a row dragged from disc 2 into the middle of disc 1 ended up after disc 1's
   rows rather than between them (measured: dropped at 1-02, landed at 1-03). A number the user
   **typed** now counts in the disc the track is being put on; a number left alone still counts where
   the track was, which is what keeps §9.22's collapse from interleaving the two discs. One line, one
   function, and the drag lands.
   One case only the browser could show: a row dragged into another disc often keeps its *per-disc*
   number by coincidence — 2-02 dropped at 1-02 is still "2" — so a changed number cannot always
   say that the user moved it, and without that knowledge it was read as a row that stayed put and
   filed after its new disc's rows. The payload now carries `moved` for rows the user has actually
   put somewhere since the last save, which is the UI stating an intent instead of the server
   inferring one. A collapse sends no `moved`, so §9.22's case is untouched.
   That also changed a case reviewed in P3: typing "1" while collapsing two discs into one used to put
   the track first of its *former* disc-2 block (seventh), and now puts it first of the album. Under
   one disc, "1" means first; the old reading was defensible only while numbers were read under the
   old discs for every purpose. The test carries the reasoning.

33. ✅ The page's own logic has tests (2026-09-26, backlog item 10, the user's call after two UI
   defects shipped green). `app.js` had grown to ~1,500 lines carrying real rules — the length
   target, the arrangement and its live renumbering, what a panel offers, the filter's folding — and
   none of it ran under a test. What it *did* have was the Python twins and the Playwright cases,
   which is why the two defects that shipped (§9.29's invisible badge, §9.31's scattered buttons)
   were caught by looking at pictures; neither a unit test nor a DOM assertion would have found them,
   and that is the honest limit of what this slice buys.
   The split is `webui/logic.mjs` — everything that computes rather than draws, exported — and
   `app.js`, which imports it. The page loads it as `<script type="module">`, which browsers do
   natively, so **there is still no build step**: the reason the module keeps the `.mjs` extension is
   that node then treats it as ESM without a `package.json`, and the repo stays free of npm. Tests
   are `node --test` with `node:assert`; `tests/test_js.py` shells out to them so `uv run pytest`
   runs everything, and skips with a reason where node is missing (CI installs it rather than
   skipping quietly).
   The twins are what earn it: `tests/shared/trim_target.json` is one table of twelve cases that
   `plan.trimmed_gap` and `logic.mjs`'s `trimTarget` are both tested against, so a rule changed on one
   side fails on the other. §9.24's rounding, §9.22's per-disc numbering, §9.32's drop semantics,
   §9.21's panel rules and §9.29's badge decision are pinned the same way.
   Caching needed one more thing than the split: a module's import URL is inside the module, where
   the index's rewriting never reached, so a new `logic.mjs` could have sat behind a cached `app.js`
   that never asked for it. `IMPORTS` in web.py versions the import when the module is served, and
   folds the imported file into the importer's own hash.
   Left browser-only, deliberately: everything that needs a DOM — the drag's pointer handling, the
   panel's rendering, the player. Testing those would mean jsdom, which is the npm dependency this
   slice exists to avoid; the catalog's sections H and K–R remain their evidence.

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

### Decisions of 2026-09-26 (the QA run and the backlog that came out of it)

- A lyric is the user's by its **bytes**, not by a flag they set; the record of what we wrote
  decides, and where there is no record, lrclib is asked about the entry we stored (§9.21).
- Deleting your own lyric gives the mark up with it, so `--refetch` can answer again (§9.21).
- The lyrics panel **writes**, and the editor never looks anything up — a save can then never be
  answered by replacing the words just typed (§9.26).
- A rejected lrclib entry is remembered **per track** and never offered for it again, which is what
  a deleted file could not say (§9.27).
- A fetch renames only the album it is fetching; the library's spelling wins and `repair` is what
  upgrades the rest (§9.23).
- `repair` decides each artist's spelling once, before it renames anything (§9.23).
- A length reference lrclib contributes is the **consensus** of its candidates, not the nearest
  one, and the query drops only the instrumental markers (§9.24).
- A single's album name follows its own track's title (§9.25).
- **Repair is reachable from the web UI, behind a confirm rather than a preview** (P10, catalog L):
  a preview would need a pass that reports without writing, which is the fetch preview's job and
  not repair's. This decision lives nowhere else.
- The fetch preview merges with what is in the library, so it shows what a fetch would write rather
  than a fresh reading of YouTube; Shift+click skips it (§9.28).
- Resetting a field drops its provenance rather than guessing it, because `auto` records the derived
  value and never its source (§9.29).
- Opening an album reconciles its lyrics with the disk; the library grid deliberately does not
  (§9.30).
- A number the user **typed** counts in the disc they are putting the track on; one left alone counts
  where the track was (§9.32).
- **No JavaScript test harness inside a feature package** (§9.31): the arithmetic lives in Python
  where it is tested and the browser cases are the evidence for the rest. Whether the repo gets one
  is open — `docs/backlog.md` item 10.
