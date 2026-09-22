"""ytalbum — turn YouTube playlists into properly tagged albums. See DESIGN.md."""


def main() -> None:
    import sys

    from .cli import main as cli_main

    sys.exit(cli_main())
