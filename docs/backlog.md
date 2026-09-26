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

## 2. The UI sends people to a terminal

The fetch log says "`ytalbum repair` unifies them", but `repair` does not exist in the web UI:
no button, no API route. A dry run exists only on the command line too. A UI user who follows
the hint has nowhere to click.

Wanted: a repair action (with the same one-line-per-rename log) and a preview for a fetch.

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

## 5. No way back from an edit

The plan keeps the derived value (`auto`) for every field a user overrides, but the UI offers
no "reset to what ytalbum found". An edited album artist is frozen out of harmonisation and
repair with no visible way to opt back in.

Wanted: a reset affordance per edited field, shown where the "from" column already says `user`.

## 6. Two silent-lag spots

Ownership of an edited sidecar and the status of a deleted one update only when a pass walks
the album (documented in DESIGN §9.21). Between passes the UI can show a ♪ for lyrics that are
gone. Safe, but a user would call it a bug.

Wanted: reconcile the album's lyrics state when the album view is opened (read-only check,
cheap), or a filesystem watcher. Item 1 removes the lag for edits made in the UI itself.

*After P8:* the lag is now only for changes made **outside** the UI, since the editor refreshes the
row and the panel itself. The panel also reads the file on every open, so opening it shows the
current words even when the row's ♪ is stale — which shrinks this item to the marker and the badge
counts rather than the words themselves.

## 7. The ⏱ chip is a diagnosis, not an action

The chip says a track is too long against its reference, never where to cut (a data limit,
DESIGN §9.8). The trim inputs are bare seconds fields.

Wanted: "play from here / set start / set end" next to the player, so the chip leads to a trim
instead of to arithmetic.
