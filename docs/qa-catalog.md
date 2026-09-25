# QA catalog

Hand-run checks from a user's point of view, aimed at the places where **features meet**:
trimming a track that has lyrics, renaming one that MusicBrainz matched, pruning an album whose
order you set yourself. The pytest suite covers the pieces; this covers the seams.

Derived from the code as of 2026-09-26 (379 tests, 246 albums in the reference library).

## How to use it

Tick a box when a case passes and write the one line that proves it. A case that fails gets the
observed behaviour instead — that line is the bug report.

**Classes**

- **R** — read-only: no file in the library and no config changes.
- **M** — reversible mutation: renames, retags, trims, lyrics, edits. Recoverable by re-fetching,
  by `ytalbum repair`, or from `.originals/`.
- **D** — potentially destructive: deletes files, or can overwrite something you wrote by hand.

**Markers** — ★ a combination the test suite does not exercise · ⚠ expected to fail

**Before starting**

```sh
mkdir -p /tmp/qa-plans && rsync -a --include='*/' --include='.ytalbum.json' --exclude='*' \
  ~/Music/YouTube\ Downloads/ /tmp/qa-plans/     # reverses every M case except deletions
ytalbum repair                                    # must report "0 album(s) tidied up" first
```

Run **R** first, then **M** on one album you do not mind rebuilding, then **D** last — and for
**D** fetch a throwaway single rather than using an album you care about. Never run a CLI job
while the web UI is working: they do not lock against each other (see G5).

---

## A. Acquisition and first contact

- [ ] **A1 · R** — dry run over an album already held
  - do: `ytalbum fetch <playlist-url> --dry-run`
  - expect: the plan is printed and nothing is written
  - invariant: the existing album's plan and files are untouched
  - evidence: `.ytalbum.json` mtime before/after

- [ ] **A2 · R★** — search without picking
  - do: `ytalbum search "<artist you own>"`, then answer "none"
  - expect: albums already in the library are marked as such
  - invariant: no folder appears
  - evidence: the listing; `ls` of the artist folder

- [ ] **A3 · M** — the documented hand-editing path
  - do: `ytalbum plan <url>` → edit `.ytalbum.json` (change the album name, swap two numbers) →
    `ytalbum download <dir>`
  - expect: your edits are what lands on disk
  - invariant: files carry the edited names; `tracktotal` matches
  - evidence: file names; `mutagen` tags

- [ ] **A4 · M★** — a single from a label channel
  - do: fetch one official video from a label channel (title ends in `| <Label>`)
  - expect: album name without the label suffix, `kind = single`
  - invariant: `drop_label` applies to album names, not only track titles
  - evidence: `plan.album`; the folder name

- [ ] **A5 · M★** — a video with no audio-only stream
  - do: fetch one, take the "no audio — choose" offer in the album view, accept `combined`
  - expect: the track re-downloads as `.m4a` and is tagged through the MP4 atoms
  - invariant: `ext` flips to `m4a`; the cover and `©lyr` are written
  - evidence: `mutagen` atom dump; the file suffix

---

## B. Trim, and everything it touches

- [ ] **B1 · M** — cut the front
  - do: play a track, drag the start handle, press *save trim*
  - expect: the file is shorter; `.originals/<video id>.opus` holds the untouched download
  - invariant: the original is byte-identical to the file before the trim
  - evidence: `ffprobe` duration; `sha1sum` against a copy taken beforehand

- [ ] **B2 · M** — "end here" (regression, fixed 2026-09-26)
  - do: play, press *end here* mid-song, adjust, press *save trim*
  - expect: playback **stops on the mark** and stays on this track; the row's trim field fills
  - invariant: the save applies to the track you were editing, never the next one
  - evidence: player title unchanged; `trim_end` in the plan

- [ ] **B3 · M** — a saved trim still skips the outro
  - do: play a track whose trim is already saved, past the end mark
  - expect: it advances to the next track, as normal listening should
  - invariant: only *unsaved* marks stop playback
  - evidence: the player title changes

- [ ] **B4 · M** — undo a trim
  - do: clear both marks, save
  - expect: the file is restored from `.originals/`
  - invariant: byte-identical to the pre-trim copy; `trimmed` back to `None`
  - evidence: `sha1sum`

- [ ] **B5 · M★** — play a trimmed track
  - do: play a track that has a saved trim
  - expect: the player loads `?o=1` (the original) and previews the cut itself
  - invariant: the head is cut once, not twice
  - evidence: `audio.src`; position ≈ `trim_start` two seconds in

- [ ] **B6 · M★⚠** — trim an `.m4a` track (needs A5)
  - do: set both marks on the m4a track and save
  - expect (suspected defect): `original_path` hardcodes `.opus` and `apply()` remuxes into
    `.trim.opus` with `-c copy`, so AAC into an Opus container should fail
  - invariant regardless: the playable file survives and the failure is reported on the track —
    never a truncated or silent file
  - evidence: `track.error`; duration unchanged; ffmpeg stderr

- [ ] **B7 · M★** — change the audio source of a trimmed track
  - do: trim a track, then switch it to `combined`
  - expect: it re-downloads, `trimmed` resets, the marks re-apply to the new original
  - evidence: `plan.trimmed`; duration

- [ ] **B8 · M★** — one trim for a whole channel
  - do: press ⇉ on a track from an uploader that appears in ≥3 albums
  - expect: every track from that uploader is trimmed, each keeping its own original
  - invariant: albums not involved are untouched
  - evidence: the job log; per-album plans

---

## C. Lyrics

- [ ] **C1 · M** — idempotence
  - do: `ytalbum lyrics` twice
  - expect: the second run asks nothing and retags nothing
  - evidence: job log line counts

- [ ] **C2 · M★** — trim a track that has synced lyrics, to a length that still matches
  - expect: the words survive or are re-matched against the new length
  - invariant: timestamps are **never** shifted by the trim (they belong to the matched recording)
  - evidence: the first timestamp in the `.lrc` before/after; `lyrics_id`

- [ ] **C3 · M★** — trim to a length nothing matches, then clear it
  - expect: the words go, then come back when the length is a known one again
  - invariant: no stale `.lrc` is left beside a track whose verdict is "none"
  - evidence: `plan.lyrics`; sidecar presence

- [ ] **C4 · M** — rename a track that has lyrics
  - expect: the `.lrc` follows the audio and the tag is rewritten from it
  - invariant: `mbid` **and** `mb_length` are both cleared (they belong together)
  - evidence: file names; the `LYRICS` tag; the plan

- [ ] **C5 · M★** — lyrics you wrote yourself
  - do: write a `.lrc` by hand, set `provenance["lyrics"] = "user"`, run `ytalbum lyrics --refetch`
  - expect: your file is untouched
  - evidence: `sha1sum` before/after

- [ ] **C6 · D★** — a hand-written `.lrc` **without** the provenance mark
  - do: as C5 but skip the provenance line, then `--refetch`
  - expect (honest): it is overwritten. Decide whether that is acceptable or whether an
    unmarked sidecar should also be protected
  - evidence: the sidecar diff

- [ ] **C7 · M** — the file is the source of truth
  - do: delete a `.lrc`, then `ytalbum download <album dir>`
  - expect: the tag loses the words too
  - evidence: `mutagen` tag absent

- [ ] **C8 · M★** — mark a track as instrumental yourself
  - do: rename a track to end in "(instrumental)", clear its lyrics status, run `ytalbum lyrics`
  - expect: the sung version's words are refused; the verdict reads "no words"
  - invariant: the marker is read from the **track** title, never the album's
  - evidence: `plan.lyrics`; no sidecar

- [ ] **C9 · R** — the lyrics panel
  - do: open an album, click ♪, click a line, let it play on
  - expect: it seeks there; the line being sung is marked as the song plays
  - invariant: the box scrolls, the page does not
  - evidence: `audio.currentTime`; the `.now` class; `window.scrollY` across a minute

---

## D. Length signals

- [ ] **D1 · R** — the ⏱ filter
  - expect: exactly the flagged albums, and the full grid again when toggled off
  - evidence: card count against `album_length_flag`

- [ ] **D2 · M★** — trim a flagged clip until it matches
  - do: pick a track whose chip is red, trim it to the known length, save
  - expect: the chip clears and the album's badge recomputes
  - invariant: the badge follows the data, with no reload needed beyond the next poll
  - evidence: `/api/state` for that album

- [ ] **D3 · M★** — replace video edits with the audio release
  - do: on an album flagged "long", delete and fetch the audio playlist of the same record
  - expect: gaps collapse to ~0 and the badge disappears
  - evidence: per-track gaps

- [ ] **D4 · R★** — an album nobody has a length for
  - expect: no badge and no chips — silence rather than a false "0:00"
  - evidence: the album view

---

## E. Update, merge, prune

- [ ] **E1 · M** — `ytalbum update --dry-run`
  - expect: reports only; unchanged albums cost one request each
  - evidence: job log; plan mtimes

- [ ] **E2 · M★** — a user order survives an update
  - do: reorder tracks in the album view, save, then `ytalbum update`
  - expect: your numbers stand; a video that appeared since joins the **end**
  - invariant: `provenance["order"] == "user"`
  - evidence: the numbering before/after

- [ ] **E3 · M★** — a disc split survives an update
  - do: set discs 1/2 on an album, save, then update
  - expect: the split stands and each disc counts from 1
  - evidence: `plan.tracks[].disc`

- [ ] **E4 · M★** — user fields against a deep update
  - do: edit a title and an artist, then `ytalbum update --deep`
  - expect: neither is overwritten by MusicBrainz
  - evidence: the `provenance` map

- [ ] **E5 · D** — prune
  - do: on an album where a video has left the source, `ytalbum prune <dir>`
  - expect: only the tracks marked `in_source: false` are deleted; the rest renumber and retag
  - invariant: `.lrc` and `.originals/` entries go with them
  - evidence: the directory listing; `tracktotal`

- [ ] **E6 · D★** — prune an album whose order you set
  - expect (open question): gaps close, so your numbers change. Confirm that is wanted, or
    make prune leave a user order alone
  - evidence: numbering before/after

- [ ] **E7 · M★** — a spelling that differs from the library's
  - do: fetch an album whose artist is spelled differently from the folder you already have
  - expect: it relocates into the existing folder; no second artist folder appears
  - evidence: `ls` of the artist folders

---

## F. Deletion

- [ ] **F1 · D** — delete one track
  - expect: audio, `.lrc` and `.originals/` entry all go; the rest renumber and retag
  - evidence: the directory listing; the plan

- [ ] **F2 · D** — delete an album holding a file you put there
  - expect: ytalbum's files go, your file and the folder stay, and the log says so
  - evidence: the folder contents; the log line

- [ ] **F3 · D★** — delete then re-fetch the same source
  - expect: a clean album with no leftovers from the previous copy
  - evidence: the file count; a fresh plan

---

## G. Jobs, concurrency, lifecycle

- [ ] **G1 · M** — cancel a running fetch
  - expect: it stops at the next safe point and the plan stays consistent; re-running resumes
  - evidence: job state; a second run completes the album

- [ ] **G2 · M★** — queue a lyrics job during a fetch
  - expect: both are write-lane jobs, so they serialise
  - invariant: they never interleave on one plan file
  - evidence: job start/finish times

- [ ] **G3 · R★** — search during a fetch
  - expect: the search answers straight away on its own lane
  - evidence: job lanes in `/api/state`

- [ ] **G4 · R** — restart while busy
  - do: `ytalbum service restart` during a job
  - expect: it refuses and says why, unless given `--force`
  - evidence: exit code; the message

- [ ] **G5 · M★⚠** — CLI and web UI writing at once
  - do: start a fetch in the web UI, then run `ytalbum lyrics` in a terminal on the same album
  - expect (suspected gap): nothing locks the two processes against each other, so the last
    writer of `.ytalbum.json` wins. Decide between a lock file and documenting the rule
  - evidence: the plan after both finish, against each job's log

- [ ] **G6 · R** — idle exit
  - do: `ytalbum serve --idle-exit 60` and leave it alone
  - expect: it exits only after a minute with no requests **and** no jobs
  - evidence: process lifetime

---

## H. Web UI and PWA

- [ ] **H1 · R★** — an open editor during a download
  - expect: the editor stays where it is; focus does not drag the viewport
  - evidence: `window.scrollY` across a library refresh

- [ ] **H2 · R** — filter, then "play matches"
  - expect: the matching songs play across albums, in grid order
  - evidence: the queue

- [ ] **H3 · R** — the jump rail and back-to-top
  - expect: the rail spans the viewport; a jump lands with the card fully visible
  - evidence: scroll offsets

- [ ] **H4 · R★** — reload after a restart
  - expect: the new content-hashed `app.js` loads; no stale UI from the service worker
  - evidence: the asset URL in the DOM

- [ ] **H5 · R★** — server gone
  - do: stop the service with the page open
  - expect: the offline banner appears and clears when the server returns
  - evidence: the banner

- [ ] **H6 · R★** — media keys with an unsaved trim
  - expect: next/previous work, and the unsaved mark is neither saved nor silently lost
  - evidence: `playerctl status`; the plan is unchanged

---

## I. Degraded modes

- [ ] **I1 · M** — without MusicBrainz or lrclib
  - do: `ytalbum fetch <url> --no-mb --no-lyrics`
  - expect: it downloads and tags from YouTube's data alone
  - evidence: provenance in the plan; no `.lrc`

- [ ] **I2 · M★** — lyrics switched off in the config
  - do: `ytalbum config --lyrics off`, then a normal fetch
  - expect: no lrclib traffic at all
  - evidence: the lyrics cache file's mtime

- [ ] **I3 · M★** — the network drops mid-lookup
  - expect: the failure is transient — the status stays unset so the track is asked again
  - invariant: a network error is never recorded as "none"
  - evidence: the plan afterwards; a re-run finds the words

- [ ] **I4 · R★** — no ffmpeg
  - do: take `ffmpeg` off `PATH`, then try a trim
  - expect: it fails loudly, on that track
  - invariant: the audio file is never damaged
  - evidence: `track.error`; the duration is unchanged

---

## Results

| Date | Cases run | Passed | Failed | Notes |
|---|---|---|---|---|
| | | | | |
