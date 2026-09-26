# QA catalog

Hand-run checks from a user's point of view, aimed at the places where **features meet**:
trimming a track that has lyrics, renaming one that MusicBrainz matched, pruning an album whose
order you set yourself. The pytest suite covers the pieces; this covers the seams.

Derived from the code as of 2026-09-26 (379 tests at the time of writing, 458 after the fixes it produced; 246 albums in the reference library).

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

- [x] **A3 · M** — the documented hand-editing path
  - do: `ytalbum plan <S3> --library "$QA"` → edit the plan (album name, swap two numbers) →
    `ytalbum download "$QA/<artist>/<album>"`
  - expect: your edits are what lands on disk
  - invariant: file names follow the edited values; `tracktotal` matches
  - evidence: file names; `mutagen` tags
  - **result 2026-09-26:** pass — hand-edited the plan (two tracks kept, numbers swapped, album renamed): folder relocated, files named from the edits, `tracknumber` 1/2 and `tracktotal` 2 in the tags

- [x] **A4 · M★** — a single from a label channel
  - do: the S1 fetch from the seed step
  - expect: album name *Viva Vendetta*, **not** *Viva Vendetta | Napalm Records*; `kind = single`
  - invariant: `drop_label` applies to album names, not only track titles
  - evidence: `plan.album`; the folder name
  - **result 2026-09-26:** pass — album *Viva Vendetta*, kind single, no label suffix

- [x] **A5 · M★** — take the audio out of the video stream
  - do: on S1, set `audio_choice` to `combined` (album view, or the `edit` action) and let it
    re-download
  - expect: the track arrives as `.m4a`, tagged through the MP4 atoms
  - invariant: `ext` flips; the cover and `©lyr` are written; the old `.opus` is removed
  - evidence: `mutagen` atom dump; the file suffix
  - note: this is how to reach the m4a path without hunting for a video that has no audio stream
  - **result 2026-09-26:** pass — the track came back as `.m4a` with MP4 atoms and the cover, and the old `.opus` was removed

---

## B. Trim, and everything it touches

- [x] **B1 · M** — cut the front
  - do: on S1, drag the start handle to 19.8 s, *save trim*
  - expect: the file is shorter; `.originals/___ci9kmRc4.opus` holds the untouched download
  - invariant: the original is byte-identical to the file before the trim
  - evidence: `ffprobe` duration; `sha1sum` against a copy taken beforehand
  - **result 2026-09-26:** pass — 470.9 s → 451.1 s, `.originals/___ci9kmRc4.opus` byte-identical to the pristine download

- [x] **B2 · M** — "end here" (regression, fixed 2026-09-26)
  - do: on S1, play, press *end here* around 408 s, adjust, *save trim*
  - expect: playback **stops on the mark** and stays on this track; the row's trim field fills
  - invariant: the save applies to the track you were editing, never the next one
  - evidence: the player title is unchanged; `trim_end` in the plan
  - **result 2026-09-26:** pass — stayed on the track, paused at 61.4 s, handle read *Song ends at 1:01*, mark unsaved

- [x] **B3 · M** — a saved trim still skips the outro
  - do: with B2 saved, play into the end mark
  - expect: it advances to the next track (or stops, if it is the last)
  - invariant: only *unsaved* marks stop playback
  - evidence: the player title
  - **result 2026-09-26:** pass — playing into a saved end advanced *Ketzerei* → *Hexenjagd*. Note: once saved, the file itself ends at the mark, so `ended` and the end-mark check coincide

- [x] **B4 · M** — undo a trim
  - do: clear both marks on S1, save
  - expect: restored from `.originals/`, byte-identical; `trimmed` back to `None`
  - invariant: clearing a trim is a restore, not a re-download — it works offline
  - evidence: `sha1sum`
  - **result 2026-09-26:** pass — clearing both marks restored the file byte-identical to the pristine download (`trimmed` back to `None`)

- [x] **B5 · M★** — play a trimmed track
  - do: play the track you trimmed in B1, from the album view
  - expect: the player loads `?o=1` (the original) and previews the cut itself
  - invariant: the head is cut once, not twice
  - evidence: `audio.src`; position ≈ `trim_start` two seconds in
  - **result 2026-09-26:** pass — the player requested `&o=1`, was served the 470.9 s original, and sat at 22.5 s after three seconds: the head is cut once

- [x] **B6 · M★⚠** — trim an `.m4a` track (needs A5)
  - do: set both marks on the m4a track from A5 and save
  - expect (suspected defect): `original_path` hardcodes `.opus` and `apply()` remuxes into
    `.trim.opus` with `-c copy`, so AAC into an Opus container should fail
  - invariant regardless: the playable file survives and the failure is reported on the track —
    never a truncated or silent file
  - invariant: the playable file survives and the failure is reported on the track
  - evidence: `track.error`; duration unchanged; ffmpeg stderr
  - **result 2026-09-26:** **FAIL, worse than predicted** — see the finding below. The trim read a stale `.opus` original left by an earlier format, wrote Ogg/Opus into the `.m4a`, and the tagger then raised an unhandled `MP4StreamInfoError`
  - **re-run after P1: pass** — the m4a trim produced a 112 s file that is still an MP4 container, the original was kept as `dGO_sx4By28.m4a`, no error

- [x] **B7 · M★** — change the audio source of a trimmed track
  - do: trim S1, then switch it to `combined`
  - expect: it re-downloads, `trimmed` resets, the marks re-apply to the new original
  - invariant: the trim points are kept while the audio underneath is replaced
  - evidence: `plan.trimmed`; duration
  - **result 2026-09-26:** **FAIL (deferred)** — switching a trimmed track to `combined` silently does not apply the trim (fresh 470.9 s file, `trimmed=None`); the **next** run applies it and corrupts the file exactly as in B6
  - **re-run after P1: pass** — switching the trimmed track back to opus and running the next pass applied the same trim in the opus container (112 s, ogg); nothing was corrupted

- [x] **B8 · M★** — one trim for a whole channel
  - do: fetch S9, then set a front trim on S1 and press ⇉
  - expect: S9 is trimmed to the same points, in its own album, keeping its own original
  - invariant: the rule keys on the **uploader**, not the artist — S1 and S9 are different
    bands sharing one label channel, while S2 is the same band on its own channel and must
    **not** be touched
  - evidence: all three plans' `trim_start`/`trim_end`; the job log
  - **result 2026-09-26:** pass on the rule — both *Napalm Records* tracks took the trim while the two *Lord Of The Lost* albums and the *xxFEUERSCHWANZxx* one were untouched, so it keys on the uploader, not the artist. It also exposed the persistence described below
  - **re-run after P1: pass** — the Napalm Records track took the trim and applied it, the Lord Of The Lost track stayed untouched, and no stale original got in the way

---

## C. Lyrics

- [x] **C1 · M** — idempotence
  - do: `ytalbum lyrics --library "$QA"` twice
  - expect: the second run asks nothing and retags nothing
  - invariant: a second pass must not rewrite a single tag
  - evidence: job log line counts
  - **result 2026-09-26:** pass — two runs in a row reported the same `6 none, 2 synced, 1 instrumental` and retagged nothing

- [x] **C2 · M★** — trim a track that has synced lyrics, to a length that still matches
  - do: on S3 track 4 (*Sex is Muss*, 285 s of file against a 217.6 s song, synced lyrics),
    trim the end towards ~218 s
  - expect: the words survive or are re-matched against the new length
  - invariant: timestamps are **never** shifted by the trim; a front trim makes the track be
    looked up again, an end trim need not
  - evidence: the first timestamp in the `.lrc`; `lyrics_id` before/after
  - **result 2026-09-26:** pass — trimming to 218 s did not shift a single timestamp; the words were **re-matched** to the lrclib entry that fits the new length (#1949288 instead of #30840489, first line 00:27.69 against 00:27.71)

- [x] **C3 · M★** — trim to a length nothing matches, then clear it
  - do: trim S3 track 4 to about 250 s — a length no entry has — then clear the trim again
  - expect: the words go, then come back when the length is a known one again
  - invariant: no stale `.lrc` beside a track whose verdict is "none"
  - evidence: `plan.lyrics`; sidecar presence
  - **result 2026-09-26:** pass — at 250 s nothing matched and the words went; clearing the trim brought the file back to 284.7 s and the original entry (#30840489) with it

- [x] **C4 · M** — rename a track that has lyrics
  - do: rename a track that has a `.lrc` in the album view, save
  - expect: the `.lrc` follows the audio and the tag is rewritten from it
  - invariant: `mbid` **and** `mb_length` are both cleared
  - evidence: file names; the `LYRICS` tag; the plan
  - **result 2026-09-26:** pass — the `.lrc` followed the rename, the tag was rewritten from it, and `mbid` **and** `mb_length` were both cleared

- [x] **C5 · M★** — lyrics you wrote yourself
  - do: write a `.lrc` by hand, set `provenance["lyrics"] = "user"`,
    `ytalbum lyrics --library "$QA" --refetch`
  - expect: your file is untouched
  - invariant: a user provenance on lyrics outranks everything, `--refetch` included
  - evidence: `sha1sum` before/after
  - **result 2026-09-26:** pass — a hand-written sidecar marked `user` came through `--refetch` byte-identical, and the tag carries those words
  - **re-run after P2:** pass — still byte-identical (`e2bda878`), status `synced`, provenance `user`

- [x] **C6 · D★** — a hand-written `.lrc` **without** the provenance mark
  - do: write a `.lrc` by hand, leave the provenance alone, then `ytalbum lyrics --library "$QA" --refetch`
  - expect (honest): it is overwritten. Decide whether an unmarked sidecar should be protected
  - invariant: whatever is decided, the plan and the sidecar must not disagree afterwards
  - evidence: the sidecar diff
  - **result 2026-09-26:** **confirmed as written** — the unmarked sidecar was overwritten by `--refetch` (`f6ed83…` → `b20f39…`). Behaviour is as predicted; whether it should be is the product decision
  - **decided and changed in P2** (DESIGN.md §9.21): a sidecar is recognised by its bytes, so the
    mark no longer has to be set by hand. Three re-runs, all pass:
    - **C6a** (edit since the last pass, tag still disagrees): kept, marked `user`, status `synced`,
      tag rewritten from the kept file, no lrclib request needed
    - **C6b** (older edit: the tag was already rewritten from it, so it *agrees* — the common case):
      lrclib was asked what entry `5073938` holds, the answer differed, file kept, marked `user`,
      status `synced`
    - **C6c** (a sidecar we wrote ourselves): replaced by `--refetch` and **not** mistaken for the
      user's — `lyrics_sha` recorded (`ecb495c0…`), provenance untouched

- [x] **C7 · M** — the file is the source of truth
  - do: delete a `.lrc`, then `ytalbum download "$QA/<album>"`
  - expect: the tag loses the words too
  - invariant: the tag never outlives the file it was copied from
  - evidence: `mutagen` tag absent
  - **result 2026-09-26:** pass — deleting the `.lrc` removed the words from the tag on the next run. Note: `plan.lyrics` still reads `synced`, so the status outlives the words it describes
  - **re-run after P2:** pass, and the note is closed — the status went `synced` → `none`, the tag is
    gone and `lyrics_sha` was cleared. `ytalbum lyrics` also stopped skipping albums with nothing to
    look up, which was the path on which a deleted sidecar went unnoticed

- [x] **C8 · M★** — an instrumental never borrows the singer's words
  - do: run the lyrics pass over S2 (*Viva Vendetta (Instrumental)*, 230 s — the same length as
    the sung recording)
  - expect: verdict "no words"; no `.lrc`
  - invariant: the marker is read from the **track** title, never the album's
  - evidence: `plan.lyrics`; the folder
  - **result 2026-09-26:** pass, and it happened at fetch time — S2 arrived as *Viva Vendetta (Instrumental)* with verdict `instrumental` and no sidecar, although the sung recording is the same 230 s

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

- [x] **D2 · M★** — trim an overlong track until its chip clears
  - do: on S3 track 4 (+67 s), trim towards the known 217.6 s
  - expect: the amber chip turns muted once the gap is under 20 s
  - invariant: the album's badge stays `3 clips` — the three snippets are *too short*, and no
    trim can lengthen them. Only replacing those files clears that badge
  - evidence: `/api/state` for the album; the chip's class in the row
  - **result 2026-09-26:** pass — trimming *Moralisch* from +28 s to 0 s turned its chip muted, and the album kept its `3 clips` badge exactly as the case predicts

- [x] **D3 · D★** — replace a video edit with the canonical audio
  - do: in the scratch library, delete S1 (471 s of film around the song) and fetch S10, the
    same recording as an audio upload
  - expect: the gap against MusicBrainz collapses from +241 s to about 0, and the chip clears
  - invariant: it must be the **same song**, not another variant — S2 is the instrumental and
    would prove nothing about replacing an edit with its release
  - evidence: `length_gap` before and after; `mb_length` unchanged at 229.8 s
  - class: destructive because it deletes an album, even a scratch one
  - **result 2026-09-26:** pass — replacing the 451 s video edit with the canonical 230 s audio collapsed the gap from +221 s to 0 s

- [x] **D4 · R★** — an album nobody has a length for
  - do: open an album whose tracks MusicBrainz and lrclib both lack (any live or fan compilation without a release match)
  - expect: no badge and no chips — silence rather than a false "0:00"
  - invariant: no reference means no claim: neither a chip nor a badge
  - evidence: the album view
  - **result 2026-09-26:** pass — Schandmaul *Wie Pech und Schwefel*: 15 rows, 0 chips, no badge

---

## E. Update, merge, prune

- [x] **E1 · R** — `ytalbum update --dry-run --library "$QA"`
  - do: `ytalbum update --dry-run --library "$QA"`
  - expect: reports only; unchanged albums cost one request each
  - invariant: no plan is written, no folder moves
  - evidence: job log; plan mtimes
  - note: run it against the scratch library — `update` has no `--artist`, so on the real one
    it would walk all 246 albums
  - **result 2026-09-26:** deferred to the M pass — an empty scratch library proves nothing, and the real one would cost 246 requests
  - **result 2026-09-26:** pass — report only, plan mtimes unchanged; 2 of 5 albums took the one-request path

- [x] **E2 · M★** — a user order survives an update
  - do: reorder tracks of S3 in the album view, save, then `ytalbum update --library "$QA"`
  - expect: your numbers stand; a video that appeared since joins the **end**
  - invariant: `provenance["order"] == "user"`
  - evidence: the numbering before/after
  - **result 2026-09-26:** pass — a hand-set order survived `update --deep` unchanged, with `provenance.order == user`

- [x] **E3 · M★** — a disc split survives an update
  - do: set discs 1/2 on S3, save, update
  - expect: the split stands and each disc counts from 1
  - invariant: a disc split is the user's, so the source may not undo it
  - evidence: `plan.tracks[].disc`
  - **result 2026-09-26:** pass — a 4/3 disc split survived `update --deep`, file names carry `1-01`…`2-03`, each disc counting from 1

- [x] **E4 · M★** — user fields against a deep update
  - do: edit a title and an artist on S3, then `ytalbum update --library "$QA" --deep`
  - expect: neither is overwritten by MusicBrainz
  - invariant: a user field is never overwritten, however confident MusicBrainz is
  - evidence: the `provenance` map
  - **result 2026-09-26:** pass — an edited title and artist both came through `update --deep` untouched

- [x] **E5 · D** — prune
  - do: mark a track `in_source: false` in the scratch plan, then `ytalbum prune "$QA/<album>"`
  - expect: only that track's files are deleted; the rest renumber and retag
  - invariant: its `.lrc` and `.originals/` entry go with it
  - evidence: the directory listing; `tracktotal`
  - **result 2026-09-26:** pass with one gap — the pruned track's audio and `.lrc` went and the rest retagged to `tracktotal` 6, but its **`.originals/` copy stayed behind**. `delete_track` removes the original; `prune` does not, so a pruned trimmed track leaves a full-size orphan
  - **re-run after P1: pass** — the pruned track's `.originals` copy went with it
  - **re-run after P3: pass** — a trimmed track pruned from the scratch album took its audio, its
    `.lrc` and `ayFhgxdRV-Q.opus` in `.originals/` with it

- [x] **E6 · D★** — prune an album whose order you set
  - do: set a custom order on S3, save, mark a track `in_source: false`, then prune it
  - expect (open question): gaps close, so your numbers change. Confirm that is wanted, or make
    prune leave a user order alone
  - invariant: whatever is decided, the tracks keep their relative order
  - evidence: numbering before/after
  - **result 2026-09-26:** **observe-only, as instructed (R-002); album restored afterwards.** The renumbering is decided by discs, not by provenance: on a **multi-disc** album prune left the gap (1, 2, 4 …), on a **single-disc** album it renumbered 1..n and closed it, rewriting the numbers the user chose. Relative order was preserved in both. Also observed: collapsing a disc split back to one disc re-sorts by (disc, number) and **reshuffles the user's arrangement**
  - **decision (P3, DESIGN.md §9.22):** the gap closes on **every** album, per disc, and the user
    order flag stays set. What the flag protects is the sequence against the *source*, and a
    deletion the user asked for is not the source; `delete_track` has always renumbered. What must
    never change is the relative order — and that is now what the code is built on, rather than
    the numbers.
  - **run to a conclusion after P3 (no longer observe-only):** pass in all three shapes.
    Single disc: dropping track 3 of 5 left the arrangement intact, numbers 1…4 with no gap, flag
    still `user`, and the victim's audio, `.lrc` and kept original gone. Multi-disc: dropping 1-02
    of a 2/2 split left `{disc 1: [1], disc 2: [1, 2]}` — closed per disc, order and flag kept
    (this is the `1, 2, 4 …` case). Split/merge round trip: a reversed arrangement survived the
    split, and collapsing it back to one disc returned exactly the same order, numbered 1…n, with
    the file names following and no `1-01` prefix left behind

- [x] **E7 · M★** — a spelling that differs from the library's
  - do: in the scratch library, edit S1's album artist to `LORD OF THE LOST` (which marks it
    yours), then fetch S2
  - expect: S2 lands in that same folder under your spelling — one artist folder, not two
  - invariant: a spelling you chose outranks MusicBrainz' when the library is harmonised
  - evidence: `ls "$QA"`; both plans' `albumartist`
  - **result 2026-09-26:** **fails as written** — a fetch does not unify the spellings: seeding produced `LORD OF THE LOST/` and `Lord Of The Lost/` side by side, and only `repair` merged them (finally onto the MusicBrainz spelling). See the phase-1 finding
  - **re-run after P4 (DESIGN.md §9.23): pass in both directions.** Shouting into a library that
    spells it properly: the fetched album adopted `Lord of the Lost`, logged one line, and the
    `LORD OF THE LOST/` folder was gone — one folder. Into a spelling the user chose for *another*
    album: the fetched album adopted `LORD OF THE LOST`, leaving `Lord of the Lost/` only for the
    album the user had not touched. Better evidence arriving: the library's spelling was kept, one
    hint named both spellings and pointed at `ytalbum repair`, and no other album was renamed.
    Following the hint's advice converges in one pass: repair decides each artist key once, before
    it renames anything, from every candidate the library holds — so three scratch albums in three
    spellings all moved onto the MusicBrainz spelling in one run, and the run after it had nothing
    to do (it needed two passes when the decision was made album by album)

---

## F. Deletion (scratch library only)

- [x] **F1 · D** — delete one track
  - do: press ✕ on a track in the album view and confirm
  - expect: audio, `.lrc` and `.originals/` entry all go; the rest renumber and retag
  - invariant: no other track loses a file; only the numbering and totals change
  - evidence: the directory listing; the plan
  - **result 2026-09-26:** pass — audio, `.lrc` and `.originals/` copy all went, the rest renumbered and `tracktotal` retagged to 6

- [x] **F2 · D** — delete an album holding a file you put there
  - do: drop a `notes.txt` into a scratch album, then delete the album
  - expect: ytalbum's files go, your file and the folder stay, and the log says so
  - invariant: ytalbum only deletes what it wrote
  - evidence: the folder contents; the log line
  - **result 2026-09-26:** pass — ytalbum's files went, my `notes.txt` and the folder stayed, and the log named the file it kept

- [x] **F3 · D★** — delete then re-fetch the same source
  - do: delete a scratch album, then fetch the same URL again
  - expect: a clean album with no leftovers from the previous copy
  - invariant: a fresh fetch starts from nothing — no orphan `.lrc`, no stale original
  - evidence: the file count; a fresh plan
  - **result 2026-09-26:** pass — the re-fetch came back with one track, a fresh plan and no stale `.originals/`; the user's file was still there

---

## G. Jobs, concurrency, lifecycle

- [x] **G1 · M** — cancel a running fetch
  - do: start the S6 fetch (56 tracks) on the scratch server, cancel after a few tracks
  - expect: it stops at the next safe point; the plan stays consistent; re-running resumes
  - invariant: a cancelled job leaves a plan that describes the files on disk
  - evidence: job state; a second run completes the album
  - **result 2026-09-26:** pass — cancelling a 56-track fetch left it `cancelled` with 3 done tracks, each with its file, one `.parts` leftover; resuming took it to 28 with no gaps

- [x] **G2 · M★** — queue a lyrics job during a fetch
  - do: start the S6 fetch on the scratch server, then press *Fetch lyrics* on another album
  - expect: both are write-lane jobs, so they serialise
  - invariant: they never interleave on one plan file
  - evidence: job start/finish times
  - **result 2026-09-26:** pass — the lyrics job sat `queued` on the write lane while the fetch ran, then ran on its own

- [x] **G3 · R★** — search during a fetch
  - do: start a fetch, then type an artist name into the search box
  - expect: the search answers straight away on its own lane
  - invariant: a read job never waits for a write job
  - evidence: job lanes in `/api/state`
  - **result 2026-09-26:** deferred to the M pass — needs a write job in flight
  - **result 2026-09-26:** pass — a search answered on the read lane in 20.7 s while the fetch kept running

- [x] **G4 · R** — restart while busy *(limitation of the case, not of the software)*
  - do: with a scratch job running, `ytalbum service restart`
  - expect: it refuses and says why, unless given `--force`
  - invariant: the refusal is the default; `--force` is the deliberate way past it
  - evidence: exit code; the message
  - note: needs an M job in flight to be meaningful; the restart itself writes nothing
  - **result 2026-09-26:** deferred to the M pass — the refusal only triggers on a *write* job; a search deliberately does not block a restart
  - **result 2026-09-26:** **not executable under the run's constraints** — the refusal only triggers on a *write* job, and the busy check always targets the installed service on the real library, which is read-only here. Observation: `service restart --port N` accepts the flag and ignores it; `restart()` calls `busy()` with no port
  - **recorded as a limitation of the case (P6):** it cannot be run without a write job on the
    *installed* service, i.e. against the real library, which every pass of this catalog forbids.
    The refusal itself is covered offline (`test_restart_refuses_while_a_job_runs`). The flag
    observation was a real fault and is fixed: `--port` and `--idle-exit` only describe the units,
    so outside `install` they are now refused with a message and exit 2 instead of being ignored

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

- [x] **H1 · M★** — an open editor during a download
  - do: open an album on the scratch server, then start a job that changes it
  - expect: the editor stays where it is; focus does not drag the viewport
  - invariant: a library refresh never moves the viewport
  - evidence: `window.scrollY` across a library refresh
  - class: the UI part is read-only, but triggering it needs a mutating job
  - **result 2026-09-26:** pass — 14 samples over 21 s of an active download: the scroll position never moved off 453 and the editor stayed open

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

- [x] **I1 · M** — without MusicBrainz or lrclib
  - do: `ytalbum fetch <S2> --library "$QA" --no-mb --no-lyrics`
  - expect: it downloads and tags from YouTube's data alone
  - invariant: both lookups are optional; the download path does not depend on them
  - evidence: provenance in the plan; no `.lrc`
  - **result 2026-09-26:** pass — `mbid` absent everywhere, every provenance `yt_title`, no `.lrc`

- [x] **I2 · M★** — lyrics switched off for the run
  - do: the same fetch with `--no-lyrics` only
  - expect: no lrclib traffic at all
  - invariant: switching lyrics off for a run leaves the configured setting alone
  - evidence: the lyrics cache file's mtime
  - note: `config --lyrics off` would change the real setup — use the flag
  - **result 2026-09-26:** pass — the lyrics cache file was not touched by the fetch

- [x] **I3 · M★** — the network drops mid-lookup
  - do: `XDG_CACHE_HOME="$QA/cache" HTTPS_PROXY=http://127.0.0.1:9 ytalbum lyrics --library "$QA"`
    — an empty cache and a dead proxy make every request fail, with no root and no cable to pull
  - expect: the failure is transient — the status stays unset so the track is asked again
  - invariant: a network error is never recorded as "none"
  - evidence: the plan afterwards; a re-run finds the words
  - **result 2026-09-26:** pass — with a cold cache behind a dead proxy, all five tracks stayed unset and **none** was recorded as `none`

- [x] **I4 · M★** — no ffmpeg
  - do: run a trim with `ffmpeg` off `PATH`
  - expect: it fails loudly, on that track
  - invariant: the audio file is never damaged; the original in `.originals/` is intact
  - evidence: `track.error`; the duration is unchanged
  - class: `apply()` copies the file into `.originals/` before ffmpeg runs, so this writes
  - **result 2026-09-26:** **invariant holds, expectation fails** — the audio was untouched (83.1 s before and after, no exception), but the failure was **not loud**: `error=None` in the plan, nothing in the job log, and a `trim_end` advertised that never happened. The pending trim was then applied silently on a later run once ffmpeg was back
  - **re-run after P1: pass** — audio untouched, `could not trim: … 'ffmpeg'` recorded on the track, and a `trim failed` event reached the job log

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

- [x] **J2 · M★** — the marker finds the *right* release
  - do: `ytalbum plan <S5> --library "$QA"`
  - expect: the album keeps "(Instrumental)" **and** matches MusicBrainz' instrumental release
  - invariant: a version marker narrows the match, it does not only block it
  - evidence: `plan.album`; `plan.mbid` resolves to a title containing "(Instrumental)"
  - **result 2026-09-26:** pass — album kept *(Instrumental)* and matched the MusicBrainz release **of the same name**; no audio downloaded

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
  - **re-run after P4: the note is closed** — harmonisation now runs in the `--dry-run` path too
    (read-only, and guarded for a dry run with no library), so the preview prints the artist and
    folder the fetch would write

- [x] **J6 · M★** — a rejected recording leaves no length behind
  - do: `ytalbum plan <S3> --library "$QA"`, look at *Ketzerei (Summer Breeze 2016)*
  - expect: the title keeps its bracket group, `mbid` is absent, and `mb_length` is absent too
  - invariant: a length belongs to the recording it was read from
  - evidence: the plan JSON
  - **result 2026-09-26:** pass — *Krieger des Mets (Wacken 2016)* and *Ketzerei (Summer Breeze 2016)* both have `mbid=None` **and** `mb_length=None`, while *Ringelpietz (mit Anfassen)*, whose brackets are part of the title, keeps both

- [x] **J7 · R★** — a wordless entry does not end the search
  - do: ask the matcher directly for S8's track 5 at 211.7 s, with a cold cache
  - expect: lrclib #1948185 (213 s, synced), not one of the 16 wordless stubs
  - invariant: lrclib's "instrumental" means nobody submitted words, not that there are none
  - evidence: the returned id and the first line of the text
  - **result 2026-09-26:** pass — #1948185, synced, *Mein lieber Herr Hauptmann…*, from a cold cache

- [x] **J8 · R★** — an instrumental refuses the sung words
  - do: ask the matcher for *Viva Vendetta (Instrumental)* at 230 s
  - expect: no words, although the sung recording is the same length
  - invariant: only the title can separate an instrumental cut from the sung one
  - evidence: `status`; `text is None`
  - **result 2026-09-26:** **the case was wrong, not the code** — lrclib's search for a title containing *(Instrumental)* returns 0 rows, so `get()` answers `None`; the *no words* verdict is made by `update_track`. Assert end to end, not at the client
  - **corrected expectation (P5, DESIGN.md §9.24):** no words **and** a length reference. Asking
    with the marker in the query is what returned nothing, so the marker is now stripped from the
    query alone. **Re-run from a cold cache against live lrclib: pass** — the query asked is
    *Viva Vendetta*, `text is None`, status `none`, `length` 230.0; the same search *with* the
    marker still returns 0 rows, which is the fault this closes

- [x] **J9 · R★** — a refused length is still remembered
  - do: ask the matcher for *Viva Vendetta* at 471 s
  - expect: no words, but a `length` comes back as the second opinion. It is the candidate
    **closest to our file**, which for a padded file is the least representative one: 248 s
    here, where eight of the nine entries say 230 s
  - invariant: that is what feeds the length chip for tracks MusicBrainz does not know
  - evidence: the returned object
  - **result 2026-09-26:** **the case was wrong, and found something** — the near miss is the candidate closest to *our* 471 s file (248 s), not the 230 s that eight of nine entries agree on. Expectation corrected below; the selector itself is now an open question
  - **decided and fixed (P5, DESIGN.md §9.24):** the reference is the consensus of the same-artist
    candidates — the commonest whole second, the median of the tied values on a tie. **Re-run from
    a cold cache against live lrclib: pass** — the nine candidates came back as 229.0, 229.8,
    229.8, 230.0 ×5 and 248.0, and the kept length is 230.0

- [x] **J10 · R★** — a repeated length is real data
  - do: in the real library, look at S6's 24 tracks of 223 s
  - expect: every one keeps its `mb_length`; the album carries no length flag
  - invariant: repetition is not evidence of a bad reference — the rule that assumed so was
    reverted on 2026-09-26
  - evidence: the plan; `album_length_flag` is `None`
  - **result 2026-09-26:** pass — 23 of 56 tracks share 222.1 s, every one kept, flag `None`

---

## K. The lyrics editor (P8, DESIGN §9.26)

Added 2026-09-26 with the editor itself. Run against a freshly seeded scratch library (S1 and S3)
on a scratch server at 8799, driven through the real page with Playwright; the sidecar, the tag
and the plan were read on disk after each step.

- [x] **K1 · M** — write lyrics for a track that has none
  - do: open S3, click the faint ♪ on track 1 (*Ketzerei*, status `none`), press "Write lyrics",
    type two timestamped lines, Save
  - expect: the words are on disk, marked as yours, and in the tag
  - invariant: nothing is looked up — an editor that asked LRCLIB could answer a save by
    replacing what was just typed
  - evidence: the `.lrc`; `lyrics`/`lyrics_sha`/`provenance` in the plan; the `LYRICS` tag
  - **result:** pass — a ♪ is now offered on every downloaded track, faint when there are no words.
    The panel said "no words yet" and offered "Write lyrics" with no Delete. After Save:
    `.lrc` written with one trailing newline, `lyrics=synced`, `lyrics_sha=5e1fb9f6e6a231de`,
    `provenance.lyrics=user`, tag identical to the file, and the job log read
    "Save your lyrics for Ketzerei"

- [x] **K2 · M** — edit LRCLIB's words
  - do: on track 4 (*Sex is Muss*, LRCLIB #30840489, 42 lines), press Edit, change one line, Save
  - expect: the file is yours from then on, with the edit in the tag too
  - invariant: the panel shows who owns the words, immediately
  - evidence: the panel header; the `.lrc`; the tag
  - **result:** pass — the textarea opened prefilled with all 42 lines; after Save the panel came
    back at once showing the edited line and a **yours** badge in place of the lrclib link, and on
    disk `provenance.lyrics=user`, a new `lyrics_sha`, the tag equal to the file. `lyrics_id` is
    deliberately kept (it is how P2 recognises a file as ours)

- [x] **K3 · M** — clear from the editor
  - do: Edit that track again, press Delete
  - expect: the `.lrc` and the tag go, the status becomes `none`, the mark is dropped
  - invariant: a clear is the same act as deleting the file by hand (§9.21)
  - evidence: the folder; the plan; the tag
  - **result:** pass — sidecar gone, `lyrics=none`, `lyrics_sha=None`, no `provenance.lyrics`, tag
    absent; the row's ♪ went faint and the panel offered "Write lyrics" again, with no wait for the
    poll. One blemish found and fixed here: the header still cited `lrclib #30840489` beside
    "no words yet", because the id is kept — the link is now shown only when there are words

- [x] **K4 · R** — a save while a pass holds the album
  - do: start a `--refetch` of S3, then POST a save for one of its tracks
  - expect: refused, with the job named; nothing written
  - invariant: a pass retags from the sidecar, so the two must not interleave
  - evidence: the status code and message; the file
  - **result:** pass — `HTTP 400: “Look up all lyrics of Sex Is Muss” is working on this album —
    wait for it, then save again`, and the user's file was untouched. Known gap, stated in
    DESIGN §9.26: a `fetch` is named by its URL and learns the album id while it runs, so the
    check cannot see it

- [x] **K5 · M** — what a later lyrics run does to both
  - do: after K1 and K3, run `--refetch` over the album
  - expect: the words written in the editor are kept; the cleared track gets LRCLIB's back
  - invariant: the mark protects a file, and a clear gives it up (§9.21)
  - evidence: both sidecars and both provenance entries
  - **result:** pass — *Ketzerei* still reads `[00:12.00] Ketzerei, written by hand` with
    `provenance.lyrics=user`; *Sex is Muss* came back with LRCLIB's own first line (without the K2
    edit) and no mark

- [x] **K6 · R** — a track with no file, and a track from another album
  - do: POST a save for a track in state `pending`, and for a `video_id` of a different album
  - expect: refused, with a message that says which
  - invariant: `sidecar_path` is the only path builder; nothing is written outside the album folder
  - evidence: the status codes
  - **result:** covered offline in `test_web.py` (400 "no file yet", 400 "no such track"), since the
    scratch album has every track downloaded

- [x] **K7 · R** — an open panel survives a refresh
  - do: keep a panel open while a job runs and the page polls
  - expect: the words stay on screen
  - invariant: a rebuild of the table must not close what you are reading
  - evidence: the panel after a poll-driven re-render
  - **result:** **found as a defect and fixed in this package** — the first save wrote the file
    correctly but the panel vanished, because every re-render rebuilt the table and dropped the
    row. Open panels are now remembered and restored after any render, which also stops a poll
    closing lyrics you are reading while a download runs

### K8–K11, the per-track actions (P9, DESIGN §9.27)

Run 2026-09-26 against a scratch library seeded fresh inside the session scratchpad (S3 only),
server on 8799, through the real page.

- [x] **K8 · M** — reject a match, and prove it stays rejected
  - do: on S3 track 4 (*Sex is Muss*, LRCLIB #30840489), press "Not these words"; then press
    "Look up again" on the same track
  - expect: the words go; the rejected entry is never taken again, even by an explicit new lookup
  - invariant: rejecting says "that entry is not this recording", which stays true; deleting the
    file only said "not now"
  - evidence: `lyrics_rejected` in the plan; the second job's log line; the panel
  - **result:** pass — after the reject: `lyrics=none`, `lyrics_id=None`, `lyrics_rejected=[30840489]`,
    sidecar and tag gone, the row's ♪ faint, and "Not these words" no longer offered (nothing left to
    reject). The following "Look up again" logged *"Sex is Muss: nothing lrclib has fits this
    recording"* — the only entry LRCLIB has for it stayed out. `lyrics_length` (218 s) is kept, so
    the ⏱ reference survives the rejection

- [x] **K9 · M** — look one track up again
  - do: on track 7 (*Moralisch*, LRCLIB #28467509), press "Look up again"
  - expect: that track alone is asked about, and the sidecar and tag are rewritten from the answer
  - invariant: no other track of the album is looked up
  - evidence: the panel header before and after; the job log
  - **result:** pass — header unchanged at `· lrclib #28467509` with the words rewritten, job labelled
    "Look up the lyrics of Moralisch (höchst verwerflich)". The offline tests cover the case where a
    track that had none gains words, since every track of this album had already been looked up

- [x] **K10 · M** — neither action touches words of the user's
  - do: write lyrics for track 1 in the editor, then POST both actions for it
  - expect: refused, and the panel offers only Edit
  - invariant: a user's words are not LRCLIB's to replace — the editor's Delete is the way back
  - evidence: two 400s; the panel's buttons; the file
  - **result:** pass — both answered `400 Ketzerei: these lyrics are yours — delete them first`, the
    file kept its `[00:05.00] mine, not lrclib's`, and the panel showed **yours** with Edit as the
    only action

- [x] **K11 · R** — the refusals
  - do: a per-track action while a pass holds the album; on a track that is not `done`; rejecting
    when there is no match to reject
  - expect: refused with a message naming the reason
  - evidence: the status codes and messages
  - **result:** covered offline in `test_web.py` (the job name in the message, "no file yet",
    "no lrclib match"); the live album has every track downloaded and no long pass to race

## L. Repair from the web UI (P10)

Added 2026-09-26. A scratch library inside the session scratchpad, seeded with one album fetched
for real and a second copy of it under a shouted spelling of the same artist (`FEUERSCHWANZ` /
`Feuerschwanz`), so repair had something to do. Server on 8799, driven through the real page.

- [x] **L1 · M** — the button, its confirm and its log
  - do: press "Repair library" in the header, read the dialog, accept
  - expect: a write-lane job whose log carries repair's own lines and its summary
  - invariant: nothing is downloaded and nothing is deleted; the dialog says so before it runs
  - evidence: the dialog text; the job's lane, label and log; the folders on disk
  - **result:** pass — the confirm showed README's paragraph (what it changes, that folders and
    files are renamed, that nothing is downloaded or deleted, that edited values are kept). The job
    came back `lane=write`, `kind=repair`, label "Repair the library", and its log ended with
    `=== Feuerschwanz — Sex Is Muss (Shouted)`, four rename/retag lines and
    **`1 album(s) tidied up`**. On disk the `FEUERSCHWANZ/` folder is gone and both albums sit under
    `Feuerschwanz/`, both now `provenance.albumartist = mb`

- [x] **L2 · R** — refused while the library is being changed
  - do: start a lyrics `--refetch`, then press Repair
  - expect: refused, naming the job in the way
  - invariant: repair renames folders across the library, so it must not run beside a writer
  - evidence: the status code and message
  - **result:** pass — `HTTP 400: “Look up all lyrics of Sex Is Muss” is running — wait for it,
    then repair`

- [x] **L3 · R** — the hint a user can now follow
  - do: read the fetch-time spelling hint
  - expect: it names both the command and the button
  - invariant: one message serves the CLI user and the UI user; neither is sent somewhere they
    cannot go
  - evidence: the log line
  - **result:** pass — "… 'ytalbum repair' (or “Repair library” in the web UI) unifies them on the
    better spelling", asserted in `test_web.py` as well so the two cannot drift apart

## Results

| Date | Cases run | Passed | Failed | Notes |
|---|---|---|---|---|
| 2026-09-26 | the 22 R cases | 17 | 0 in the software; 2 cases mis-specified (J8, J9) | E1, G3 and G4 deferred to the M pass. No file in the real library changed. |
| 2026-09-26 | the M cases (A–E, H, J) | 28 | 3 real faults, 1 case impossible as written | The faults: a trim re-cut from the previous format's original and corrupted the file (B6/B7); a failed trim was recorded nowhere (I4); prune left the kept original behind (E5). E7 failed as written — a fetch did not unify the spelling. Scratch library only. |
| 2026-09-26 | the D cases (D3, E6, F1–F3, C6) | 6 | 0 | All in the scratch library, after the plan-file backup described above. E6 was observe-only on instruction and is now run to a conclusion; C6 confirmed the unmarked-sidecar overwrite it predicted, which P2 then changed. |
| 2026-09-26 | the L cases (repair from the web UI) | 3 | 0 | Scratch library with two spellings of one artist; the shouted folder was gone afterwards. Nothing measured on the real library: repair is a no-op there today (I-014, I-015). |
| 2026-09-26 | the K8–K11 cases (per-track lyrics actions) | 4 | 0 | Driven through the real page on a scratch library inside the session scratchpad. The rejected entry stayed rejected across an explicit new lookup. |
| 2026-09-26 | the K cases (the lyrics editor) | 7 | 1 defect found in the package under test (K7), 1 blemish (K3) | Both fixed before the commit. Driven through the real page against a freshly seeded scratch library. |
| 2026-09-26 | re-runs after the fixes, `1e3da95..456d83e` | B6, B7, B8, I4, E5, E6, C5, C6, C7, E7, J5, J8, J9 + the split/merge round trip | all pass | Nine commits: trim integrity and its two mirrors, the lyrics ownership contract and its follow-up, order and prune, artist unification, repair's one-pass decision, the consensus length reference. 458 tests at the end, from 379. |

### Evidence methods that lied

Five of my own checks produced a false result, or none at all, before the software did anything
wrong. Use these forms:

- **A scripted doc edit that matches nothing succeeds.** `str.replace` returns the string
  unchanged when its target is absent, so a heredoc that rewrites a paragraph, writes the file and
  exits 0 can leave the document untouched — twice here, both times because an earlier edit in the
  same session had already changed the text being matched. Two commits therefore claimed
  documentation that was never written. Assert the target appears exactly once before writing, and
  grep for the new text afterwards.
- **The specimens carry the scars of earlier cases.** Case C4 renamed a track to *Sex is Muss
  (QA rename)*, which silently took it out of lrclib's reach — so a later lyrics case run against
  that specimen reported a FAIL that was nothing to do with the code under test. Check a specimen's
  current title and status before using it as evidence, or pick one no earlier case touched.
- **File-state comparison**: `find … -printf '%T@ %p\n' | sort`. Unsorted, directory order
  alone changes the hash and a dry run looks like a write.
- **Page state**: `queue`, `qi`, `state` are top-level `let` bindings — global, but **not**
  properties of `window`. `window.queue` is `undefined`, and the throw leaves your promise
  unresolved so the call hangs.
- **Process checks**: never `pgrep -f`/`pkill -f` with a pattern that appears in your own
  command line — it matches the shell running it. `pkill` that way killed the shell instead of
  the server. Check the **port** (`ss -ltnp | grep 8799`) or the log.

### Findings from the M pass

- **A fetch can leave the library split; only `repair` unifies it.** Seeding produced
  `LORD OF THE LOST/` and `Lord Of The Lost/` side by side, because `_harmonize_artist` aligns
  the album being fetched to the library but never rewrites the albums already there. Observed
  again in the other direction when a MusicBrainz-spelled plan arrived after a repair had
  settled on the YouTube spelling. Each `repair` converged correctly (finally on
  *Lord of the Lost*), so the fault is that a fetch alone does not.
  *Fixed in P4 (DESIGN.md §9.23): the fetched album adopts the library's spelling either way, and
  when it is itself the better evidence one line says so and leaves the upgrade to `repair`.*
- **An album artist can disagree with its own track artists.** After the first repair,
  *Viva Vendetta* read `albumartist='Lord Of The Lost'` (`yt_title`, borrowed from the other
  album) while its only track read `'Lord of the Lost'` (`mb`). The evidence order weighs
  provenance on the album field, and the MusicBrainz evidence sat on the track. *Fixed in P4: an
  album is made consistent with its own tracks — most common track artist, key-equal, spelled
  differently, MB behind it — before the library is consulted, and `repair` does the same step, or
  the fetch-time hint could not keep its promise.* It resolved
  once an MB-spelled album joined the library, but the intermediate state was wrong.
- **A single's album name keeps what a track title drops.** Fetching the DOMINUM video gave
  the album `The Dead Don't Die (feat. @xxFEUERSCHWANZxx)` — the raw `@handle` and the feat.
  group — while the track title was cleaned to `The Dead Don't Die (feat. xxFEUERSCHWANZxx)`.
  Same class as the label suffix fixed in `5f49752`: album naming does not share all of
  `clean_title`'s hygiene.

### The trim/format defect, from B6, B7 and B8

Four faults, one root. `original_path()` names the kept original `<video id>.opus` whatever the
track's format is, and `apply()` always cuts into `.trim.opus` with `-c copy`.

1. **Silent container corruption.** A track that was trimmed as `.opus`, then switched to
   `combined`, is trimmed again from the **stale Opus original**: ffmpeg copies happily and the
   result is written over the `.m4a`. `file` reports *Ogg data, Opus audio* for a `.m4a`.
2. **An unhandled exception, and a stuck album.** `tag_file` dispatches on the suffix, so it
   calls `MP4()` on Ogg content and raises `MP4StreamInfoError`. It is not caught: the run
   aborts, and **every later run on that album raises the same thing** until someone removes
   the file by hand. Switching the format back recovers it (re-download plus cleanup).
3. **The plan and the disk disagree in silence.** After the crash the plan said `state=done`,
   `trimmed=None`, `error=None`, while the file was 388 s of Ogg named `.m4a`.
4. **A cleanly failed trim is not recorded.** With no stale original, ffmpeg refuses the m4a
   ("Unsupported codec id in stream 0"), `run()` sets `track.error` — and then nothing saves
   the plan, because no signature changed. The plan keeps `error=None` and advertises a trim
   that never happened. In the web UI the warning goes to the logger, not the job log, so the
   user sees **nothing at all**.

And it outlives the format switch: `.originals/dGO_sx4By28.opus` still held AAC after the track
was switched back to `.opus`, so every future trim of that track fails on a wrong-codec
original. B8 ran into this while otherwise passing.

### Two more from phase 3

- **A broken trim is retried for ever.** The DOMINUM track left with a wrong-codec original
  re-attempted its trim on *every* pass over that library — each `lyrics` or `download` run
  logged the same ffmpeg refusal. Nothing records the failure, so nothing ever gives up on it.
- **A status can outlive the words it describes.** After the sidecar was deleted (C7) the tag
  lost the lyrics, as designed, but `plan.lyrics` still read `synced`. *Fixed in P2.*

### From phase 4

- **Any failed trim is silent, not just the m4a one.** I4 removed ffmpeg from `PATH`: the trim
  failed, the audio was untouched, and nothing recorded it — no `error` in the plan, nothing in
  the job log, and the plan still advertising the trim. This is the same fault as the m4a case
  reached from a realistic direction, since ffmpeg is an optional dependency.
- **A failed trim is retried until it succeeds.** The trim I4 left pending was applied on a
  later unrelated run, once ffmpeg was back, so a track can change length long after the edit
  that asked for it.

### From phase 5

- **Prune keeps the original, delete removes it.** `delete_track` unlinks `.originals/<id>.opus`;
  `prune` does not. A pruned track that had been trimmed leaves a full-size orphan nothing will
  ever read again.
- **Merging discs reshuffles a user's order.** Setting every track back to disc 1 re-sorts by
  (disc, number), so tracks that were 2-01…2-03 land among the disc-1 numbers. A split is
  reversible on paper but not in arrangement.

### Smaller observations, not cases

- `HEAD /` answers `501 Unsupported method` — GET and POST are implemented, HEAD is not. Only
  matters to health checks and proxies.
- A dry run prints the album artist *before* library harmonisation, so the preview can differ
  from what a real fetch writes (J5: `LORD OF THE LOST` previewed, `Lord of the Lost` written).
  *Fixed in P4: harmonisation runs in the dry-run path too.*
- A title carrying a bracket marker can zero out the lrclib search, so an instrumental track
  may end up with no length reference at all (J8).
