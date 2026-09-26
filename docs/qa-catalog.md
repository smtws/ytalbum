# QA catalog

Hand-run checks from a user's point of view, aimed at the places where **features meet**:
trimming a track that has lyrics, renaming one that MusicBrainz matched, pruning an album whose
order you set yourself. The pytest suite covers the pieces; this covers the seams.

Derived from the code as of 2026-09-26 (379 tests, 246 albums in the reference library).

## How to use it

Tick a box when a case passes and write the one line that proves it. A case that fails gets the
observed behaviour instead — that line is the bug report.

### What the classes mean

- **R** — reads the library, never writes to it. May still write *outside* it: the MusicBrainz
  and lrclib caches, the job history in a running server, `.parts/` scratch during a download.
- **M** — changes the library and can be undone: renames, retags, trims, lyrics, edits,
  relocations.
- **D** — deletes audio, or can overwrite something you wrote by hand.

**Markers** — ★ a combination the test suite does not exercise · ⚠ expected to fail

### Safety: use a scratch library, not a backup

An earlier version of this file claimed that copying the `.ytalbum.json` files was enough to
undo the **M** cases. **It is not.** Those cases rewrite tags inside audio files, rename and
move files and folders, create and delete `.lrc` sidecars, and cut audio. Restoring only the
plans would leave the plan describing files that no longer match it — a worse state than the
one you started from.

So: **every M and D case runs against a scratch library**, which is a directory that starts
empty and can be deleted at any point.

```sh
export QA=/tmp/ytalbum-qa
mkdir -p "$QA"

# seed it — about nine tracks; S9 and S10 are fetched later, by B8 and D3 alone
ytalbum fetch --library "$QA" 'https://www.youtube.com/watch?v=___ci9kmRc4'   # S1
ytalbum fetch --library "$QA" 'https://www.youtube.com/watch?v=ITVwzDlOg3M'   # S2
ytalbum fetch --library "$QA" 'https://www.youtube.com/playlist?list=PLfX9CI0JhSo6PJ4yWuk2Cn5HfvRwmOMrV'  # S3

# a second server, so the installed one keeps serving the real library untouched
ytalbum serve --library "$QA" --port 8799
```

Every command in an M or D case carries `--library "$QA"`; every UI action happens on
`http://127.0.0.1:8799`. `ytalbum config` is never used during QA — it would change the real
setup. Throwing the scratch library away is `rm -rf /tmp/ytalbum-qa` (ask first if that rule
applies to you).

**R cases run against the real library**, because several of them are about scale — 246 albums,
the jump rail, the ⏱ filter finding 11 albums out of 246.

Caches are shared with normal use by default, which keeps the load on MusicBrainz and lrclib
low. For a case that must reach the live service (J7–J9, I3), force a cold start with
`XDG_CACHE_HOME="$QA/cache"`.

### Specimens

| id | what it is | address |
|---|---|---|
| S1 | *Viva Vendetta (Official Video)*, 471 s — label suffix in the title, 4 minutes of film around a 3:50 song, no lyrics at that length | `watch?v=___ci9kmRc4` |
| S2 | *Viva Vendetta (Instrumental)*, 230 s, from the band's own channel — the same song without singing | `watch?v=ITVwzDlOg3M` |
| S3 | Feuerschwanz — *Sex Is Muss*, 7 tracks — holds three snippets of 83–104 s against songs of 215–251 s, and a track whose title keeps "(Summer Breeze 2016)" | `playlist?list=PLfX9CI0JhSo6PJ4yWuk2Cn5HfvRwmOMrV` |
| S4 | Sabaton — *Heroes (Track Commentary Version)*, 13 commentary clips carrying the album's track names | `playlist?list=OLAK5uy_mj-XiLHvQGkosFBvRsGnDX6mPhhW2YEo8` |
| S5 | Lord of the Lost — *OPVS NOIR Vol. 1 (Instrumental)*, 11 tracks, and MusicBrainz has the instrumental release too | `playlist?list=OLAK5uy_lqRyvQ-Tz6zW8YJkIb8AGJbU8NFb5Ygwc` |
| S6 | Lord of the Lost — *Judas*, the **audio** release: 56 tracks, 24 of them exactly 223 s | `playlist?list=OLAK5uy_kvKX_bTPTXGRmCQy3wcwy0UD2By9j5y2w` |
| S7 | Visions of Atlantis — *Delta*: MusicBrainz credits the release "Visions **Of** Atlantis" while the artist is "Visions of Atlantis" | `playlist?list=OLAK5uy_kOH7P46M_xmvvnVSUPNiMTvaivBBjn9vk` |
| S8 | Feuerschwanz — *Blöde Frage, Saufgelage* (on *Best Of*): lrclib holds 18 entries for it, 16 of them wordless stubs, one synced (#1948185) at 213 s. Used only as a **query**, never fetched | `playlist?list=OLAK5uy_lZouiZ8ft8t-95Br3K3al8ttP_N5d2hyk` |
| S9 | DOMINUM — *The Dead Don't Die*, 246 s: a second video **uploaded by the same channel as S1** (Napalm Records), carrying the same label ident at the front. Fetched only for B8 | `watch?v=dGO_sx4By28` |
| S10 | *Viva Vendetta*, 230 s — the **sung** album recording as its own audio upload, against MusicBrainz' 229.8 s. The canonical counterpart to S1's 471 s film. Fetched only for D3 | `watch?v=Z2UO4FsFGFM` |

Prefix each with `https://www.youtube.com/`.

### Order

R first (free), then M against the scratch library, then D. Never run a CLI job while a server
is working on the same library — nothing locks them against each other (G5).

---

## A. Acquisition and first contact

- [x] **A1 · R** — dry run over an album already held
  - do: `ytalbum fetch <S3> --dry-run`
  - expect: the plan is printed and nothing is written
  - invariant: the real library's plan and files are untouched
  - evidence: `.ytalbum.json` mtime before/after
  - note: the MusicBrainz cache *is* written; that is outside the library
  - **result 2026-09-26:** pass — sorted plan list identical, 269 folders before and after. My first attempt hashed **unsorted** `find` output and reported a phantom write

- [x] **A2 · R★** — search without picking
  - do: `ytalbum search "Feuerschwanz"`, answer "none"
  - expect: albums already in the library are marked as such
  - invariant: no folder appears
  - evidence: the listing; `ls ~/Music/YouTube\ Downloads/Feuerschwanz`
  - **result 2026-09-26:** pass — every held album marked `✓ in library`, exit 0, no folder created

- [ ] **A3 · M** — the documented hand-editing path
  - do: `ytalbum plan <S3> --library "$QA"` → edit the plan (album name, swap two numbers) →
    `ytalbum download "$QA/<artist>/<album>"`
  - expect: your edits are what lands on disk
  - invariant: file names follow the edited values; `tracktotal` matches
  - evidence: file names; `mutagen` tags

- [ ] **A4 · M★** — a single from a label channel
  - do: the S1 fetch from the seed step
  - expect: album name *Viva Vendetta*, **not** *Viva Vendetta | Napalm Records*; `kind = single`
  - invariant: `drop_label` applies to album names, not only track titles
  - evidence: `plan.album`; the folder name

- [ ] **A5 · M★** — take the audio out of the video stream
  - do: on S1, set `audio_choice` to `combined` (album view, or the `edit` action) and let it
    re-download
  - expect: the track arrives as `.m4a`, tagged through the MP4 atoms
  - invariant: `ext` flips; the cover and `©lyr` are written; the old `.opus` is removed
  - evidence: `mutagen` atom dump; the file suffix
  - note: this is how to reach the m4a path without hunting for a video that has no audio stream

---

## B. Trim, and everything it touches

- [ ] **B1 · M** — cut the front
  - do: on S1, drag the start handle to 19.8 s, *save trim*
  - expect: the file is shorter; `.originals/___ci9kmRc4.opus` holds the untouched download
  - invariant: the original is byte-identical to the file before the trim
  - evidence: `ffprobe` duration; `sha1sum` against a copy taken beforehand

- [ ] **B2 · M** — "end here" (regression, fixed 2026-09-26)
  - do: on S1, play, press *end here* around 408 s, adjust, *save trim*
  - expect: playback **stops on the mark** and stays on this track; the row's trim field fills
  - invariant: the save applies to the track you were editing, never the next one
  - evidence: the player title is unchanged; `trim_end` in the plan

- [ ] **B3 · M** — a saved trim still skips the outro
  - do: with B2 saved, play into the end mark
  - expect: it advances to the next track (or stops, if it is the last)
  - invariant: only *unsaved* marks stop playback
  - evidence: the player title

- [ ] **B4 · M** — undo a trim
  - do: clear both marks on S1, save
  - expect: restored from `.originals/`, byte-identical; `trimmed` back to `None`
  - invariant: clearing a trim is a restore, not a re-download — it works offline
  - evidence: `sha1sum`

- [ ] **B5 · M★** — play a trimmed track
  - do: play the track you trimmed in B1, from the album view
  - expect: the player loads `?o=1` (the original) and previews the cut itself
  - invariant: the head is cut once, not twice
  - evidence: `audio.src`; position ≈ `trim_start` two seconds in

- [ ] **B6 · M★⚠** — trim an `.m4a` track (needs A5)
  - do: set both marks on the m4a track from A5 and save
  - expect (suspected defect): `original_path` hardcodes `.opus` and `apply()` remuxes into
    `.trim.opus` with `-c copy`, so AAC into an Opus container should fail
  - invariant regardless: the playable file survives and the failure is reported on the track —
    never a truncated or silent file
  - invariant: the playable file survives and the failure is reported on the track
  - evidence: `track.error`; duration unchanged; ffmpeg stderr

- [ ] **B7 · M★** — change the audio source of a trimmed track
  - do: trim S1, then switch it to `combined`
  - expect: it re-downloads, `trimmed` resets, the marks re-apply to the new original
  - invariant: the trim points are kept while the audio underneath is replaced
  - evidence: `plan.trimmed`; duration

- [ ] **B8 · M★** — one trim for a whole channel
  - do: fetch S9, then set a front trim on S1 and press ⇉
  - expect: S9 is trimmed to the same points, in its own album, keeping its own original
  - invariant: the rule keys on the **uploader**, not the artist — S1 and S9 are different
    bands sharing one label channel, while S2 is the same band on its own channel and must
    **not** be touched
  - evidence: all three plans' `trim_start`/`trim_end`; the job log

---

## C. Lyrics

- [ ] **C1 · M** — idempotence
  - do: `ytalbum lyrics --library "$QA"` twice
  - expect: the second run asks nothing and retags nothing
  - invariant: a second pass must not rewrite a single tag
  - evidence: job log line counts

- [ ] **C2 · M★** — trim a track that has synced lyrics, to a length that still matches
  - do: on S3 track 4 (*Sex is Muss*, 285 s of file against a 217.6 s song, synced lyrics),
    trim the end towards ~218 s
  - expect: the words survive or are re-matched against the new length
  - invariant: timestamps are **never** shifted by the trim; a front trim makes the track be
    looked up again, an end trim need not
  - evidence: the first timestamp in the `.lrc`; `lyrics_id` before/after

- [ ] **C3 · M★** — trim to a length nothing matches, then clear it
  - do: trim S3 track 4 to about 250 s — a length no entry has — then clear the trim again
  - expect: the words go, then come back when the length is a known one again
  - invariant: no stale `.lrc` beside a track whose verdict is "none"
  - evidence: `plan.lyrics`; sidecar presence

- [ ] **C4 · M** — rename a track that has lyrics
  - do: rename a track that has a `.lrc` in the album view, save
  - expect: the `.lrc` follows the audio and the tag is rewritten from it
  - invariant: `mbid` **and** `mb_length` are both cleared
  - evidence: file names; the `LYRICS` tag; the plan

- [ ] **C5 · M★** — lyrics you wrote yourself
  - do: write a `.lrc` by hand, set `provenance["lyrics"] = "user"`,
    `ytalbum lyrics --library "$QA" --refetch`
  - expect: your file is untouched
  - invariant: a user provenance on lyrics outranks everything, `--refetch` included
  - evidence: `sha1sum` before/after

- [ ] **C6 · D★** — a hand-written `.lrc` **without** the provenance mark
  - do: write a `.lrc` by hand, leave the provenance alone, then `ytalbum lyrics --library "$QA" --refetch`
  - expect (honest): it is overwritten. Decide whether an unmarked sidecar should be protected
  - invariant: whatever is decided, the plan and the sidecar must not disagree afterwards
  - evidence: the sidecar diff

- [ ] **C7 · M** — the file is the source of truth
  - do: delete a `.lrc`, then `ytalbum download "$QA/<album>"`
  - expect: the tag loses the words too
  - invariant: the tag never outlives the file it was copied from
  - evidence: `mutagen` tag absent

- [ ] **C8 · M★** — an instrumental never borrows the singer's words
  - do: run the lyrics pass over S2 (*Viva Vendetta (Instrumental)*, 230 s — the same length as
    the sung recording)
  - expect: verdict "no words"; no `.lrc`
  - invariant: the marker is read from the **track** title, never the album's
  - evidence: `plan.lyrics`; the folder

- [x] **C9 · R** — the lyrics panel
  - do: in the real library, open an album, click ♪, click a line, let it play on
  - expect: it seeks there; the line being sung is marked as the song plays
  - invariant: the box scrolls, the page does not
  - evidence: `audio.currentTime`; the `.now` class; `window.scrollY` across a minute
  - **result 2026-09-26:** pass — clicked 1:28 → seek 89.6 s, that line marked, 1:32 marked six seconds later, the box scrolled and the page did not

---

## D. Length signals

- [x] **D1 · R** — the ⏱ filter (real library)
  - do: press the ⏱ button in the library head, then press it again
  - expect: exactly the flagged albums, and the full grid again when toggled off
  - invariant: filtering changes what is shown, never what is stored
  - evidence: card count against `album_length_flag`
  - **result 2026-09-26:** pass — 12 of 246 while filtered, 246 after toggling off

- [ ] **D2 · M★** — trim an overlong track until its chip clears
  - do: on S3 track 4 (+67 s), trim towards the known 217.6 s
  - expect: the amber chip turns muted once the gap is under 20 s
  - invariant: the album's badge stays `3 clips` — the three snippets are *too short*, and no
    trim can lengthen them. Only replacing those files clears that badge
  - evidence: `/api/state` for the album; the chip's class in the row

- [ ] **D3 · D★** — replace a video edit with the canonical audio
  - do: in the scratch library, delete S1 (471 s of film around the song) and fetch S10, the
    same recording as an audio upload
  - expect: the gap against MusicBrainz collapses from +241 s to about 0, and the chip clears
  - invariant: it must be the **same song**, not another variant — S2 is the instrumental and
    would prove nothing about replacing an edit with its release
  - evidence: `length_gap` before and after; `mb_length` unchanged at 229.8 s
  - class: destructive because it deletes an album, even a scratch one

- [x] **D4 · R★** — an album nobody has a length for
  - do: open an album whose tracks MusicBrainz and lrclib both lack (any live or fan compilation without a release match)
  - expect: no badge and no chips — silence rather than a false "0:00"
  - invariant: no reference means no claim: neither a chip nor a badge
  - evidence: the album view
  - **result 2026-09-26:** pass — Schandmaul *Wie Pech und Schwefel*: 15 rows, 0 chips, no badge

---

## E. Update, merge, prune

- [ ] **E1 · R** — `ytalbum update --dry-run --library "$QA"`
  - do: `ytalbum update --dry-run --library "$QA"`
  - expect: reports only; unchanged albums cost one request each
  - invariant: no plan is written, no folder moves
  - evidence: job log; plan mtimes
  - note: run it against the scratch library — `update` has no `--artist`, so on the real one
    it would walk all 246 albums
  - **result 2026-09-26:** deferred to the M pass — an empty scratch library proves nothing, and the real one would cost 246 requests

- [ ] **E2 · M★** — a user order survives an update
  - do: reorder tracks of S3 in the album view, save, then `ytalbum update --library "$QA"`
  - expect: your numbers stand; a video that appeared since joins the **end**
  - invariant: `provenance["order"] == "user"`
  - evidence: the numbering before/after

- [ ] **E3 · M★** — a disc split survives an update
  - do: set discs 1/2 on S3, save, update
  - expect: the split stands and each disc counts from 1
  - invariant: a disc split is the user's, so the source may not undo it
  - evidence: `plan.tracks[].disc`

- [ ] **E4 · M★** — user fields against a deep update
  - do: edit a title and an artist on S3, then `ytalbum update --library "$QA" --deep`
  - expect: neither is overwritten by MusicBrainz
  - invariant: a user field is never overwritten, however confident MusicBrainz is
  - evidence: the `provenance` map

- [ ] **E5 · D** — prune
  - do: mark a track `in_source: false` in the scratch plan, then `ytalbum prune "$QA/<album>"`
  - expect: only that track's files are deleted; the rest renumber and retag
  - invariant: its `.lrc` and `.originals/` entry go with it
  - evidence: the directory listing; `tracktotal`

- [ ] **E6 · D★** — prune an album whose order you set
  - do: set a custom order on S3, save, mark a track `in_source: false`, then prune it
  - expect (open question): gaps close, so your numbers change. Confirm that is wanted, or make
    prune leave a user order alone
  - invariant: whatever is decided, the tracks keep their relative order
  - evidence: numbering before/after

- [ ] **E7 · M★** — a spelling that differs from the library's
  - do: in the scratch library, edit S1's album artist to `LORD OF THE LOST` (which marks it
    yours), then fetch S2
  - expect: S2 lands in that same folder under your spelling — one artist folder, not two
  - invariant: a spelling you chose outranks MusicBrainz' when the library is harmonised
  - evidence: `ls "$QA"`; both plans' `albumartist`

---

## F. Deletion (scratch library only)

- [ ] **F1 · D** — delete one track
  - do: press ✕ on a track in the album view and confirm
  - expect: audio, `.lrc` and `.originals/` entry all go; the rest renumber and retag
  - invariant: no other track loses a file; only the numbering and totals change
  - evidence: the directory listing; the plan

- [ ] **F2 · D** — delete an album holding a file you put there
  - do: drop a `notes.txt` into a scratch album, then delete the album
  - expect: ytalbum's files go, your file and the folder stay, and the log says so
  - invariant: ytalbum only deletes what it wrote
  - evidence: the folder contents; the log line

- [ ] **F3 · D★** — delete then re-fetch the same source
  - do: delete a scratch album, then fetch the same URL again
  - expect: a clean album with no leftovers from the previous copy
  - invariant: a fresh fetch starts from nothing — no orphan `.lrc`, no stale original
  - evidence: the file count; a fresh plan

---

## G. Jobs, concurrency, lifecycle

- [ ] **G1 · M** — cancel a running fetch
  - do: start the S6 fetch (56 tracks) on the scratch server, cancel after a few tracks
  - expect: it stops at the next safe point; the plan stays consistent; re-running resumes
  - invariant: a cancelled job leaves a plan that describes the files on disk
  - evidence: job state; a second run completes the album

- [ ] **G2 · M★** — queue a lyrics job during a fetch
  - do: start the S6 fetch on the scratch server, then press *Fetch lyrics* on another album
  - expect: both are write-lane jobs, so they serialise
  - invariant: they never interleave on one plan file
  - evidence: job start/finish times

- [ ] **G3 · R★** — search during a fetch
  - do: start a fetch, then type an artist name into the search box
  - expect: the search answers straight away on its own lane
  - invariant: a read job never waits for a write job
  - evidence: job lanes in `/api/state`
  - **result 2026-09-26:** deferred to the M pass — needs a write job in flight

- [ ] **G4 · R** — restart while busy
  - do: with a scratch job running, `ytalbum service restart`
  - expect: it refuses and says why, unless given `--force`
  - invariant: the refusal is the default; `--force` is the deliberate way past it
  - evidence: exit code; the message
  - note: needs an M job in flight to be meaningful; the restart itself writes nothing
  - **result 2026-09-26:** deferred to the M pass — the refusal only triggers on a *write* job; a search deliberately does not block a restart

- [ ] **G5 · M★⚠** — CLI and server writing at once
  - do: start a fetch on the scratch server, then run `ytalbum lyrics --library "$QA"` in a
    terminal against the same album
  - expect (suspected gap): nothing locks the two processes, so the last writer of
    `.ytalbum.json` wins. Decide between a lock file and documenting the rule
  - invariant: whatever is decided, a plan must never be left describing files that do not exist
  - evidence: the plan afterwards against each job's log

- [x] **G6 · R** — idle exit
  - do: `ytalbum serve --library "$QA" --port 8799 --idle-exit 60`, leave it alone
  - expect: it exits only after a minute with no requests **and** no jobs
  - invariant: a job in flight keeps the server alive past its idle timeout
  - evidence: process lifetime
  - **result 2026-09-26:** pass — log reads `idle for 60s, stopping`, port free. `pgrep -f` matched its own shell and claimed the opposite: check the **port**

---

## H. Web UI and PWA

- [ ] **H1 · M★** — an open editor during a download
  - do: open an album on the scratch server, then start a job that changes it
  - expect: the editor stays where it is; focus does not drag the viewport
  - invariant: a library refresh never moves the viewport
  - evidence: `window.scrollY` across a library refresh
  - class: the UI part is read-only, but triggering it needs a mutating job

- [x] **H2 · R** — filter, then "play matches" (real library)
  - do: filter the library for a song title, press *play matches*
  - expect: the matching songs play across albums, in grid order
  - invariant: playing from a filter plays what the filter showed, in that order
  - evidence: the queue
  - **result 2026-09-26:** pass — 3 cards, button `▶ Play 3 tracks`, queue held exactly those three

- [x] **H3 · R** — the jump rail and back-to-top (real library, 246 albums)
  - do: press a letter in the jump rail, then the back-to-top button
  - expect: the rail spans the viewport; a jump lands with the card fully visible
  - invariant: a jump lands with the card fully visible under the sticky header
  - evidence: scroll offsets
  - **result 2026-09-26:** pass — rail 864 px of a 1080 px viewport; jump to S landed Sabaton at top 88 against a header bottom of 65; back-to-top settled at 0

- [x] **H4 · R★** — reload after a restart
  - do: restart the scratch server, reload its page
  - expect: the new content-hashed `app.js` loads; no stale UI from the service worker
  - invariant: a restart never serves a stale `app.js` from the service worker
  - evidence: the asset URL in the DOM
  - **result 2026-09-26:** pass — `/` is `no-store` and cites the on-disk hashes (app.js `aa3c375da3`, style.css `361034385a`); assets are `no-cache`; the worker is network-first and skips `/api/*`

- [x] **H5 · R★** — server gone
  - do: stop the scratch server with its page open
  - expect: the offline banner appears and clears when the server returns
  - invariant: the page keeps working as a viewer while the server is away
  - evidence: the banner
  - **result 2026-09-26:** pass — banner within seconds of the server dying, page still usable, cleared on recovery; console held only `ERR_CONNECTION_REFUSED`

- [x] **H6 · R★** — media keys with an unsaved trim
  - do: set an end mark without saving, press the media key for the next track, then for the
    previous one; afterwards press ▶ on the album card to start it afresh
  - expect (specified from the code, confirm it holds): going next and back **keeps** the mark,
    because the queue holds it; playing the album afresh rebuilds the queue from the plan and
    the mark is gone. A reload loses it too
  - invariant: nothing reaches the disk without *save trim* — in every one of those paths the
    plan is byte-identical
  - evidence: the end handle after each step; `sha1sum` of `.ytalbum.json` throughout
  - **result 2026-09-26:** pass — next+previous kept the 31.09 s mark and the plan's sha; replaying the album dropped it. The handle stays *visible* either way (it then marks the track end)

---

## I. Degraded modes

- [ ] **I1 · M** — without MusicBrainz or lrclib
  - do: `ytalbum fetch <S2> --library "$QA" --no-mb --no-lyrics`
  - expect: it downloads and tags from YouTube's data alone
  - invariant: both lookups are optional; the download path does not depend on them
  - evidence: provenance in the plan; no `.lrc`

- [ ] **I2 · M★** — lyrics switched off for the run
  - do: the same fetch with `--no-lyrics` only
  - expect: no lrclib traffic at all
  - invariant: switching lyrics off for a run leaves the configured setting alone
  - evidence: the lyrics cache file's mtime
  - note: `config --lyrics off` would change the real setup — use the flag

- [ ] **I3 · M★** — the network drops mid-lookup
  - do: `XDG_CACHE_HOME="$QA/cache" HTTPS_PROXY=http://127.0.0.1:9 ytalbum lyrics --library "$QA"`
    — an empty cache and a dead proxy make every request fail, with no root and no cable to pull
  - expect: the failure is transient — the status stays unset so the track is asked again
  - invariant: a network error is never recorded as "none"
  - evidence: the plan afterwards; a re-run finds the words

- [ ] **I4 · M★** — no ffmpeg
  - do: run a trim with `ffmpeg` off `PATH`
  - expect: it fails loudly, on that track
  - invariant: the audio file is never damaged; the original in `.originals/` is intact
  - evidence: `track.error`; the duration is unchanged
  - class: `apply()` copies the file into `.originals/` before ffmpeg runs, so this writes

---

## J. Version markers and matchers, end to end

Today's failures, each as a case. All but J10 need no downloads: `--dry-run` and `plan` produce
the metadata, and the lyrics matcher can be asked directly.

- [x] **J1 · R★** — a release of other recordings is not our album
  - do: `ytalbum fetch <S4> --dry-run`
  - expect: the album stays *Heroes (Track Commentary Version)*; no MusicBrainz release is
    accepted for it
  - invariant: `core()` may drop the brackets, but a version marker must match
  - evidence: the printed plan; `mbid` absent
  - **result 2026-09-26:** pass — album stayed *Heroes (Track Commentary Version)*, provenance YTM, no release accepted

- [ ] **J2 · M★** — the marker finds the *right* release
  - do: `ytalbum plan <S5> --library "$QA"`
  - expect: the album keeps "(Instrumental)" **and** matches MusicBrainz' instrumental release
  - invariant: a version marker narrows the match, it does not only block it
  - evidence: `plan.album`; `plan.mbid` resolves to a title containing "(Instrumental)"

- [x] **J3 · R★** — an edition marker is still normalised
  - do: `ytalbum fetch <S6> --dry-run`
  - expect: YouTube's "(Deluxe Version)" becomes MusicBrainz' "Judas (Deluxe Digital Edition)"
  - invariant: editions hold the same recordings; versions do not
  - evidence: the printed album name
  - **result 2026-09-26:** pass — YouTube's *(Deluxe Version)* became MusicBrainz' *Judas (Deluxe Digital Edition)*

- [x] **J4 · R★** — a credit's typography is not the artist's name
  - do: `ytalbum fetch <S7> --dry-run`
  - expect: album artist *Visions of Atlantis* although the release credit shouts "Of"
  - invariant: only case and punctuation may be corrected this way
  - evidence: the printed plan
  - **result 2026-09-26:** pass — album artist *Visions of Atlantis* against a release credited *Visions Of Atlantis*

- [x] **J5 · R★** — a label is not part of an album name
  - do: `ytalbum fetch <S1> --dry-run`
  - expect: *Viva Vendetta*, not *Viva Vendetta | Napalm Records*
  - invariant: the label belongs to the uploader, never to the album
  - evidence: the printed plan
  - **result 2026-09-26:** pass — album *Viva Vendetta*, kind single. Note: a dry run prints the artist **before** harmonisation (*LORD OF THE LOST*), so the preview is not the outcome

- [ ] **J6 · M★** — a rejected recording leaves no length behind
  - do: `ytalbum plan <S3> --library "$QA"`, look at *Ketzerei (Summer Breeze 2016)*
  - expect: the title keeps its bracket group, `mbid` is absent, and `mb_length` is absent too
  - invariant: a length belongs to the recording it was read from
  - evidence: the plan JSON

- [x] **J7 · R★** — a wordless entry does not end the search
  - do: ask the matcher directly for S8's track 5 at 211.7 s, with a cold cache
  - expect: lrclib #1948185 (213 s, synced), not one of the 16 wordless stubs
  - invariant: lrclib's "instrumental" means nobody submitted words, not that there are none
  - evidence: the returned id and the first line of the text
  - **result 2026-09-26:** pass — #1948185, synced, *Mein lieber Herr Hauptmann…*, from a cold cache

- [ ] **J8 · R★** — an instrumental refuses the sung words
  - do: ask the matcher for *Viva Vendetta (Instrumental)* at 230 s
  - expect: no words, although the sung recording is the same length
  - invariant: only the title can separate an instrumental cut from the sung one
  - evidence: `status`; `text is None`
  - **result 2026-09-26:** **the case was wrong, not the code** — lrclib's search for a title containing *(Instrumental)* returns 0 rows, so `get()` answers `None`; the *no words* verdict is made by `update_track`. Assert end to end, not at the client

- [ ] **J9 · R★** — a refused length is still remembered
  - do: ask the matcher for *Viva Vendetta* at 471 s
  - expect: no words, but a `length` comes back as the second opinion. It is the candidate
    **closest to our file**, which for a padded file is the least representative one: 248 s
    here, where eight of the nine entries say 230 s
  - invariant: that is what feeds the length chip for tracks MusicBrainz does not know
  - evidence: the returned object
  - **result 2026-09-26:** **the case was wrong, and found something** — the near miss is the candidate closest to *our* 471 s file (248 s), not the 230 s that eight of nine entries agree on. Expectation corrected below; the selector itself is now an open question

- [x] **J10 · R★** — a repeated length is real data
  - do: in the real library, look at S6's 24 tracks of 223 s
  - expect: every one keeps its `mb_length`; the album carries no length flag
  - invariant: repetition is not evidence of a bad reference — the rule that assumed so was
    reverted on 2026-09-26
  - evidence: the plan; `album_length_flag` is `None`
  - **result 2026-09-26:** pass — 23 of 56 tracks share 222.1 s, every one kept, flag `None`

---

## Results

| Date | Cases run | Passed | Failed | Notes |
|---|---|---|---|---|
| 2026-09-26 | the 22 R cases | 17 | 0 in the software; 2 cases mis-specified (J8, J9) | E1, G3 and G4 deferred to the M pass. No file in the real library changed. |

### Evidence methods that lied

Three of my own checks produced a false result before the software did anything wrong. Use
these forms:

- **File-state comparison**: `find … -printf '%T@ %p\n' | sort`. Unsorted, directory order
  alone changes the hash and a dry run looks like a write.
- **Page state**: `queue`, `qi`, `state` are top-level `let` bindings — global, but **not**
  properties of `window`. `window.queue` is `undefined`, and the throw leaves your promise
  unresolved so the call hangs.
- **Process checks**: never `pgrep -f`/`pkill -f` with a pattern that appears in your own
  command line — it matches the shell running it. `pkill` that way killed the shell instead of
  the server. Check the **port** (`ss -ltnp | grep 8799`) or the log.

### Smaller observations, not cases

- `HEAD /` answers `501 Unsupported method` — GET and POST are implemented, HEAD is not. Only
  matters to health checks and proxies.
- A dry run prints the album artist *before* library harmonisation, so the preview can differ
  from what a real fetch writes (J5: `LORD OF THE LOST` previewed, `Lord of the Lost` written).
- A title carrying a bracket marker can zero out the lrclib search, so an instrumental track
  may end up with no length reference at all (J8).
