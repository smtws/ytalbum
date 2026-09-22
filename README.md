# ytalbum

Turn a YouTube playlist into a properly tagged Opus album. Design and status: [DESIGN.md](DESIGN.md).

```sh
uv sync
uv run ytalbum config --library ~/Music/YouTube   # once; or pass --library per run
uv run ytalbum fetch  <playlist-or-video-url>     # plan + download + tag
uv run ytalbum fetch  <url> --dry-run             # just show what would be written
uv run ytalbum plan   <url>                       # write .ytalbum.json into the album folder, edit it …
uv run ytalbum download <album-folder>            # … then download from the edited plan
uv run pytest                                     # offline tests (fixtures in design-fixtures/)
```

Re-running `fetch` or `download` resumes: finished tracks are skipped, failed ones retried.
Needs `ffmpeg` and a JavaScript runtime for yt-dlp (deno or node; `ytalbum config` shows which one is used).
