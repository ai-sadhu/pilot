"""Tests for SetAdminPasswordCommand."""

from __future__ import annotations

from pathlib import Path

import pytest

from pilot.commands.sites.set_admin_password import SetAdminPasswordCommand
from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.exceptions import BenchError

_BENCH_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "redis": {"cache_port": 13000, "queue_port": 11000},
    "admin": {"enabled": True, "password": ""},
}


def _make_bench(tmp_path: Path) -> Bench:
    bench_dir = tmp_path / "bench"
    bench_dir.mkdir(parents=True, exist_ok=True)
    bench = Bench(BenchConfig._from_dict(_BENCH_DATA), bench_dir)
    bench.config.write(bench.path)
    return bench


def _run_cmd(bench: Bench, password: str | None = None) -> SetAdminPasswordCommand:
    cmd = SetAdminPasswordCommand(bench=bench, password=password)
    cmd.run()
    return cmd


# ---------------------------------------------------------------------------
# Explicit --password flag
# ---------------------------------------------------------------------------


def test_explicit_strong_password_is_saved(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)

    _run_cmd(bench, password="Str0ng!pass")

    config = BenchConfig.read(bench.path)
    assert config.admin.verify_password("Str0ng!pass")


def test_explicit_weak_password_raises_before_saving(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)

    with pytest.raises(BenchError, match="at least 8 characters"):
        _run_cmd(bench, password="weak")

    # Password unchanged (empty string still fails verify)
    config = BenchConfig.read(bench.path)
    assert not config.admin.verify_password("weak")


# ---------------------------------------------------------------------------
# Blank input + TTY stdout → auto-generate safely
# ---------------------------------------------------------------------------


def test_blank_prompt_generates_a_password(tmp_path: Path, monkeypatch, capsys) -> None:
    """Pressing Enter at the prompt on a real TTY must generate and save a valid password."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    bench = _make_bench(tmp_path)
    _run_cmd(bench, password=None)

    out = capsys.readouterr().out
    assert "Generated admin password:" in out

    # Extract generated password from output and verify it was persisted
    prefix = "Generated admin password:"
    generated = next(line for line in out.splitlines() if prefix in line).split(prefix)[-1].strip()
    config = BenchConfig.read(bench.path)
    assert config.admin.verify_password(generated)


def test_blank_prompt_generated_password_is_stored(tmp_path: Path, monkeypatch) -> None:
    """The auto-generated password must be hashed and stored in bench.toml."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    bench = _make_bench(tmp_path)
    _run_cmd(bench, password=None)

    # token_urlsafe(12) is always ≥ 16 chars.
    # Verify by checking it was stored (non-empty hash) rather than policy-validating
    # the hash directly, since the hash itself won't pass validate_admin_password.
    config = BenchConfig.read(bench.path)
    assert config.admin.password  # non-empty hash was written


# ---------------------------------------------------------------------------
# Blank input + non-TTY stdout → refuse with a clear BenchError
# ---------------------------------------------------------------------------


def test_non_tty_stdout_raises_bench_error(tmp_path: Path, monkeypatch) -> None:
    """Without a TTY on stdout the command must refuse to auto-generate a password.

    CI pipelines and wrapper processes capture stdout as logs, so emitting a
    credential there would be a silent exposure. The error message must clearly
    tell the user to supply --password explicitly.
    """
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    bench = _make_bench(tmp_path)
    with pytest.raises(BenchError, match="--password"):
        _run_cmd(bench, password=None)


def test_non_tty_stdout_error_mentions_non_interactive(tmp_path: Path, monkeypatch) -> None:
    """The error message must name 'non-interactive' so users understand the context."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    bench = _make_bench(tmp_path)
    with pytest.raises(BenchError, match="non-interactive"):
        _run_cmd(bench, password=None)


def test_non_tty_stdout_leaves_password_unchanged(tmp_path: Path, monkeypatch) -> None:
    """A failed non-TTY run must not write anything to bench.toml."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)

    bench = _make_bench(tmp_path)
    with pytest.raises(BenchError):
        _run_cmd(bench, password=None)

    config = BenchConfig.read(bench.path)
    assert not config.admin.password  # unchanged — still the empty string from _BENCH_DATA


def test_tty_stdout_with_blank_input_generates_password(tmp_path: Path, monkeypatch, capsys) -> None:
    """When stdout IS a TTY, blank input must generate and print the password."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    bench = _make_bench(tmp_path)
    _run_cmd(bench, password=None)

    out = capsys.readouterr().out
    assert "Generated admin password:" in out
    config = BenchConfig.read(bench.path)
    assert config.admin.password  # a hash was written
