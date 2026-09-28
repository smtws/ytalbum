"""0.9.1: the last release under this name says so, and changes nothing else.

The whole of the change is one line of text and a user agent that reports the truth. The point of
these cases is the *and changes nothing else*: a deprecation notice that broke a pipeline would be a
worse goodbye than no notice at all, so the line goes to stderr and stdout stays byte-for-byte what
0.9.0 wrote.
"""
from __future__ import annotations

import ytalbum
from ytalbum import cli


def test_the_notice_names_the_successor_and_says_this_is_the_last_one():
    assert "noaap" in ytalbum.NOTICE
    assert "github.com/smtws/noaap" in ytalbum.NOTICE
    assert "0.9.1" in ytalbum.NOTICE and "last release" in ytalbum.NOTICE


def test_it_goes_to_stderr_and_stdout_is_untouched(capsys, tmp_path, monkeypatch):
    """`ytalbum recycle list` on an empty library, because it prints to stdout and nothing else."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    assert cli.main(["recycle", "list", "--library", str(tmp_path)]) == 0

    out, err = capsys.readouterr()
    assert ytalbum.NOTICE in err
    assert "noaap" not in out, "stdout is what a pipe reads; it says exactly what 0.9.0 said"


def test_every_command_says_it_once(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    cli.main(["recycle", "list", "--library", str(tmp_path)])

    assert capsys.readouterr().err.count(ytalbum.NOTICE) == 1


def test_the_user_agents_report_the_version_that_is_installed():
    """Both said `0.1` from the first release to the last, and MusicBrainz — which asks for a
    contact address so it can reach whoever is misbehaving — was given a repository that had been
    renamed. Fixing it in the final release is the point of having one."""
    from ytalbum import lyrics, mb

    assert lyrics.USER_AGENT == mb.USER_AGENT == ytalbum.user_agent()
    assert ytalbum.user_agent() == f"ytalbum/{ytalbum.version()} ( https://github.com/smtws/ytalbum )"
    assert "0.1 " not in ytalbum.user_agent()
    assert "Tordt/YT-Downloads" not in ytalbum.user_agent()


def test_the_seed_note_is_left_alone():
    """It lands in a public MusicBrainz edit note. Nothing about a rename belongs in someone
    else's database, and an edit already submitted says what it said."""
    from ytalbum.mb import SEED_NOTE

    assert "Seeded by ytalbum (https://github.com/smtws/ytalbum)" in SEED_NOTE
    assert "noaap" not in SEED_NOTE


def test_the_notice_can_never_be_what_fails_a_run(tmp_path, monkeypatch, capsys):
    """Found by the existing suite: the line runs before anything guards a closed pipe.

    `ytalbum recycle list | head` closes the pipe under us, and `_recycle_list` has caught that
    since P47 — but the notice sits above it in `main`, where nothing did. An unguarded `print`
    there turns a working pipe into a traceback: a deprecation notice breaking the thing it is
    saying goodbye to.

    Only stderr is made to fail here. `test_listing_survives_being_piped_into_head` covers the
    stdout half, and it must stay the one that does: its recovery points fd 1 at `/dev/null`, which
    in an early test would take pytest's own capture with it for the rest of the run.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    class Gone:
        def write(self, *a):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            pass

    monkeypatch.setattr("sys.stderr", Gone())

    assert cli.main(["recycle", "list", "--library", str(tmp_path)]) == 0
    assert "the recycle bin is empty" in capsys.readouterr().out, "and the command still did its job"
