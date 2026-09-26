# Backlog

What is not broken but missing for someone who lives in the web UI. Written 2026-09-26 by the
reviewer after the QA run (`docs/qa-catalog.md`, fixes `1e3da95..bd0fe6f`, v0.2.0), from the
code and the run's evidence rather than from clicking through the UI. Ordered by how much a
daily user would feel each one. The owner agreed to start with item 1.

## 1. Lyrics editor — DONE (P8, DESIGN §9.26)

The lyrics panel is read-only ("click to read") while the whole ownership contract
(DESIGN §9.21) is about files a user writes by hand. To fix one line or add words LRCLIB lacks,
a user must find the audio file on disk, create a sidecar with the same stem plus `.lrc`, write
LRC syntax by hand, and run a pass before the UI shows the words as theirs.

Wanted: edit and create lyrics in the panel. Saving writes the sidecar, sets the user mark,
records the hash, rewrites the tag and derives the status from the text — the same step the
contract already performs for files found on disk, just triggered from the UI. Clearing removes
the sidecar with the P2 semantics (status `none`, mark dropped, `--refetch` may bring LRCLIB's
back). No lag: the panel shows ownership immediately.

**Done:** the ♪ button opens a panel that reads *and* writes, and it is now shown for a track with
no words at all (faint) and for one LRCLIB calls instrumental, so lyrics can be created rather than
only read. `POST /api/save_lyrics` runs as a write job, refuses while another job holds that album,
and refuses a track that is not `done`. Saving writes the sidecar, marks it yours, records the hash,
derives `synced`/`plain` from the text and rewrites the tag; an empty save clears. Nothing is looked
up, so the editor can never replace your words with LRCLIB's.

## 2. The UI sends people to a terminal — DONE (P10: repair; P11: the preview)

The fetch log says "`ytalbum repair` unifies them", but `repair` does not exist in the web UI:
no button, no API route. A dry run exists only on the command line too. A UI user who follows
the hint has nowhere to click.

Wanted: a repair action (with the same one-line-per-rename log) and a preview for a fetch.

**Done in P10:** a "Repair library" button beside "Update library", running the same `Service.repair`
as a write job, its one-line-per-change log and "N album(s) tidied up" summary landing in the job
log. It asks first, with README's paragraph about what repair does, because it renames folders and
files across the whole library. The fetch-time spelling hint now names the button as well as the
command, in one sentence that serves both kinds of user. **Done in P11:** the preview a URL already got is now the outcome — it merges with what is in the
library instead of showing a fresh reading (so it no longer promises names a fetch would not write),
says whether the album is already here, marks tracks that have left the source, and can be skipped
with Shift+click on Go so it never becomes a compulsory click.

## 3. Refetching lyrics is all or nothing — DONE (P9, DESIGN §9.27)

Shift-click on "Fetch lyrics" refetches the whole album. After fixing one track's title the
natural wish is "look this one up again", and the only way to reject a bad match is deleting
the file on disk.

Wanted: per-track "look up again" and "not these words", the latter behaving like deleting the
sidecar.

**Done, and the second action is stronger than "like deleting the sidecar":** rejecting remembers
the entry's id on the track (`lyrics_rejected`), so no later lookup can choose it again — a pass and
a `--refetch` included, which deleting the file never achieved. The next best candidate is taken
straight away if one fits. Neither action is offered for words marked as yours; the editor's Delete
is that path.

## 4. Reordering by typing numbers

Typed positions land correctly since P3, but typing numbers into 56 rows is a poor way to
reorder an album. No drag and drop; the other rows renumber only after save, so mid-edit the
column shows contradictions.

Wanted: drag to reorder, rows renumbering live.

## 5. No way back from an edit — DONE (P12, DESIGN §9.29)

The plan keeps the derived value (`auto`) for every field a user overrides, but the UI offers
no "reset to what ytalbum found". An edited album artist is frozen out of harmonisation and
repair with no visible way to opt back in.

Wanted: a reset affordance per edited field, shown where the "from" column already says `user`.

**Done:** the badge that says "you" is the button. It restores the value from `auto`, drops the USER
mark, and saves through the ordinary edit path so folder, file names and tags follow. The order flag
resets too, lifting the flag without renumbering anything now. Lyrics are not included — the
editor's Delete is their way back. In this library 59 of 246 albums and 83 tracks would show the
affordance.

## 6. Two silent-lag spots — DONE (P13, DESIGN §9.30)

Ownership of an edited sidecar and the status of a deleted one update only when a pass walks
the album (documented in DESIGN §9.21). Between passes the UI can show a ♪ for lyrics that are
gone. Safe, but a user would call it a bug.

Wanted: reconcile the album's lyrics state when the album view is opened (read-only check,
cheap), or a filesystem watcher. Item 1 removes the lag for edits made in the UI itself.

*After P8:* the lag is now only for changes made **outside** the UI, since the editor refreshes the
row and the panel itself. The panel also reads the file on every open, so opening it shows the
current words even when the row's ♪ is stale — which shrinks this item to the marker and the badge
counts rather than the words themselves.

**Done in P13:** opening an album reconciles its lyrics against the disk before the view is drawn,
so a sidecar edited or deleted outside ytalbum is recognised at once; a write job then makes it
durable and rewrites the tag. When plan and files agree nothing is written and no job exists. The
grid is deliberately not reconciled (246 albums per render); its counts catch up when an album is
opened. Measured cost on a 56-track album: about 2 ms added to the open.

## 7. The ⏱ chip is a diagnosis, not an action — DONE (P14, DESIGN §9.31)

The chip says a track is too long against its reference, never where to cut (a data limit,
DESIGN §9.8). The trim inputs are bare seconds fields.

Wanted: "play from here / set start / set end" next to the player, so the chip leads to a trim
instead of to arithmetic.

**Done:** marking from playback existed already; what was missing was the arithmetic. The trim bar
now shows what the pending marks would leave against the length MusicBrainz or LRCLIB knows, in the
chip's own colours, updating as the marks move — so the gap can be watched closing before saving.
Plus *▶ from start* to hear the start mark, a line saying when the untouched original is playing, and
marks rounded to a tenth of a second.

## 8. Documentation cleanup — LAST BUT ONE

Added 2026-09-26 by the owner, to run after every code item above is done. README, DESIGN and the
catalog grew by nine slices and four catalog sections in one day, each written as the change
landed. Read them once as a newcomer would: remove what describes states that no longer exist,
merge sentences that say the same thing twice, make the README's feature list and command table
match the UI as it is now (editor, per-track lyrics actions, repair button, preview, reset
badges), check every count, every "§9.x" pointer and every "fixed in Px" note, and keep DESIGN's
slice log as the history it is rather than rewriting it.

## 9. Retake the screenshots — LAST

Added 2026-09-26 by the owner. `docs/screenshots/` and the README's images show the UI before the
lyrics editor, the reset badges, the repair button and the preview states existed. Retake them at
the same sizes on the final code. Rule, unchanged: every screenshot shows **My Dark Lullabies**
only — the repo does not display full artist discographies.
