# Security

## What this program is

ytalbum runs on your own machine. It reads public YouTube pages and the MusicBrainz API,
writes audio files and one plan file per album into a folder you choose, and serves a web UI.
It stores no passwords and has no accounts.

## The web UI has no authentication

This is a deliberate design decision, not an oversight: `ytalbum serve` binds
**`127.0.0.1` by default**, where the operating system already decides who may connect.

`--host 0.0.0.0` removes that boundary. Anyone who can reach the port can then download into
your library, delete albums and read every file in it. Put it behind a reverse proxy with
authentication, or leave it on localhost.

What is in place either way: writing calls need an `X-Ytalbum` header and a JSON content type
(so another website cannot drive it through your browser), the `Host` header must be ours (DNS
rebinding), files are served by album and video id only — never by a path from the request —
a strict CSP applies, and thumbnails are fetched by the server so the page never talks to
Google.

## Cookies

`cookies_from_browser` hands yt-dlp your browser's YouTube session so age-restricted videos
and the bot check work. That session is read at request time and never copied into the
library, the plan files or the logs. A `cookies.txt` you point at stays wherever you put it —
treat that file as a password, because it is one.

## Reporting a vulnerability

Use **[private vulnerability reporting](https://github.com/smtws/ytalbum/security/advisories/new)**
on this repository, or write to info@smt-webservices.de. Please do not open a public issue for
a security problem. Expect a slow but real answer: this is a personal project, not a product.
