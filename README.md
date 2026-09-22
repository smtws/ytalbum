# ytalbum

Turn a YouTube playlist into a properly tagged Opus album. Design and status: [DESIGN.md](DESIGN.md).

```sh
uv sync
uv run ytalbum config --library ~/Music/YouTube   # once; or pass --library per run
uv run ytalbum fetch  <playlist-or-video-url>     # plan + download + tag
uv run ytalbum fetch  <channel-url>               # list its releases/playlists, pick (--pick 1,3-5 / --all)
uv run ytalbum search "Artist"                   # find the artist's albums/playlists, pick which to fetch
uv run ytalbum update [--dry-run]                 # re-check every album in the library, fetch what's new
uv run ytalbum fetch  <url> --dry-run             # just show what would be written
uv run ytalbum plan   <url>                       # write .ytalbum.json into the album folder, edit it …
uv run ytalbum download <album-folder>            # … then download from the edited plan
uv run ytalbum serve                              # web UI on http://localhost:8765 (--host 0.0.0.0 for the LAN, no login!)
uv run pytest                                     # offline tests (fixtures in design-fixtures/)
```

MusicBrainz is used to correct names, years, covers and tracklists when it knows the
music (`--no-mb` or `musicbrainz = false` in the config to skip); nothing is dropped when
it does not. Responses are cached in `~/.cache/ytalbum/`.

Re-running `fetch` or `download` resumes: finished tracks are skipped, failed ones retried.
YouTube throttles heavy use with a bot check ("Sign in to confirm you're not a bot").
ytalbum then stops, changes nothing, and asks you to run it again later (exit code 3).

Age-restricted videos are skipped unless cookies are configured:
`ytalbum config --cookies-from-browser firefox` (or `--cookies-file cookies.txt`).

Some streams (e.g. age-restricted videos, even with a login) need a proof-of-origin token
like a browser has. Set up the token generator once (Node ≥ 20, versions must match the
installed plugin, currently 2.0.0); `ytalbum config` shows it as "po tokens". ytalbum then
starts a local token server (127.0.0.1:4416) whenever it reads or downloads, and the server
stops itself after 5 idle minutes (`pot_mode = "script"` or `"off"` in the config to change;
log in `~/.cache/ytalbum/pot-server.log`):

```sh
git clone --single-branch --branch 2.0.0 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git .pot-provider
(cd .pot-provider/server && npm ci && npx tsc)
```

Needs `ffmpeg` and a JavaScript runtime for yt-dlp (deno or node; `ytalbum config` shows which one is used).
