# Contributing

By taking part here you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

**Bug reports and ideas are very welcome. Pull requests are read, but rarely merged as they
arrive** — this is a personal project that doubles as an evaluation of what an autonomous
coding AI can build and maintain (see the README), so a change usually gets reimplemented
here rather than pulled in. That is not a judgement of your patch; it is what the project is
for. If that makes a PR not worth your time, an issue describing the problem is worth just as
much to me.

## Reporting something

The useful ones say what the source was and what came out:

- the URL you gave it (playlist, video, channel) or the artist you searched for,
- what ytalbum produced — the artist/title it chose, the folder, the error,
- what it should have been,
- the output of `ytalbum config` if it looks like a setup problem (it prints no secrets).

A wrong name is the most valuable report this project gets: every one of them so far
uncovered a rule that was wrong for a whole class of videos, not just that one.

## Running the tests

```sh
uv sync
uv run pytest        # ~380 tests, a few seconds
```

They answer from responses recorded in `design-fixtures/`, so they need **no network, no
credentials and no ffmpeg**, and they keep working when YouTube starts refusing requests.

The suite covers the pieces; [docs/qa-catalog.md](docs/qa-catalog.md) is a hand-run checklist
for the seams between them — trimming a track that has lyrics, pruning an album whose order you
set yourself — with each case marked read-only, reversible, or destructive.

## Keeping yt-dlp current

yt-dlp decides whether this project works at all: YouTube changes, yt-dlp follows. Dependabot
opens a weekly PR for it (and for nothing else). Note what CI can and cannot tell you about
such a bump — the tests answer from recorded fixtures, so a green run proves the yt-dlp API
we call still exists, **not** that YouTube still works. That needs one real fetch:

```sh
uv run ytalbum fetch "https://www.youtube.com/playlist?list=…" --dry-run --no-mb
```

## The rules this codebase follows

They are in [DESIGN.md](DESIGN.md) §10, and they exist because the two previous versions of
this project died without them:

1. **Fix wrong data where it enters,** not where it shows up. If a name is wrong on a card,
   trace it back to the parser or the lookup before touching the display.
2. **Every bug gets a fixture test first** — capture the JSON, write the failing test, then
   fix it. No live-only tests, no debug scripts in the repo root.
3. **Never tune for one artist.** If a rule needs a special case for a single video, it is
   the wrong rule. Two failures in a row on the same symptom mean the assumption is wrong,
   not that a third layer is missing.
4. **Measure before deciding.** Several features here exist in the shape they do — and
   several plausible ones do not exist at all — because a measurement contradicted the plan.

A patch that follows those four will be understood immediately, whoever or whatever wrote it.

## If an AI wrote your patch

Say so in the PR — no judgement attached, it is literally the subject of this project. Please
also say which model and whether you ran the tests yourself. Unverified generated code is the
one kind of contribution that costs more than it gives.
