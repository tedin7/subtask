"""`subtask status [ID]` skips the full parser but prints what the parser path prints."""

import pytest

from subtask import cli


@pytest.mark.parametrize(
    ("args", "plain"),
    [
        (["status"], True),
        (["status", "st-abc"], True),
        (["status", "--help"], False),
        (["status", "-h"], False),
        (["status", "a", "b"], False),
        (["wait", "st-abc"], False),
        ([], False),
    ],
)
def test_plain_status_only_for_status_without_options(args, plain):
    assert cli._plain_status(args) is plain


@pytest.mark.parametrize("args", [["status"], ["status", "st-0000000000"]])
def test_quick_status_prints_what_main_prints(home, monkeypatch, capsys, args):
    from subtask import core

    monkeypatch.setattr(cli, "core", core)  # the fixture reloaded core: use the same module on both paths
    monkeypatch.setattr("sys.argv", ["subtask", *args])
    quick = cli.run()
    quick_out = capsys.readouterr()
    monkeypatch.setattr(cli, "_plain_status", lambda _args: False)
    slow = cli.run()
    assert (quick, quick_out) == (slow, capsys.readouterr())
