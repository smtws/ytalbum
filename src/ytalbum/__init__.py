"""ytalbum — turn YouTube playlists into properly tagged albums. See DESIGN.md.

**This program is now called noaap: https://github.com/smtws/noaap.** 0.9.1 is the last release
under this name. Nothing here stops working and nothing in a library has to be converted — noaap
reads this settings file, accepts the `YTALBUM_*` variables and writes the same `.ytalbum.json`.
"""

SUCCESSOR = "https://github.com/smtws/noaap"
NOTICE = f"ytalbum is now noaap ({SUCCESSOR}); 0.9.1 is the last release under this name."


def version() -> str:
    """The installed version, or "0" when run from a checkout that was never installed."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as installed

    try:
        return installed("ytalbum")
    except PackageNotFoundError:
        return "0"


def user_agent() -> str:
    """What this calls itself to LRCLIB and MusicBrainz. Both ask for a contact address, so it is
    the one string that has to be right: it is how they reach whoever is misbehaving."""
    return f"ytalbum/{version()} ( https://github.com/smtws/ytalbum )"


def main() -> None:
    import sys

    from .cli import main as cli_main

    sys.exit(cli_main())
