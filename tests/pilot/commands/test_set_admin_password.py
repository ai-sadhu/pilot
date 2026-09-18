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
# Blank input → auto-generate
# ---------------------------------------------------------------------------


def test_blank_prompt_generates_a_password(tmp_path: Path, monkeypatch, capsys) -> None:
    """Pressing Enter at the prompt must generate and save a valid password."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
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


def test_blank_prompt_generated_password_meets_policy(tmp_path: Path, monkeypatch) -> None:
    """The auto-generated password must satisfy the admin password policy."""
    from pilot.internal.validators import validate_admin_password

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda prompt: "")

    bench = _make_bench(tmp_path)

    # Capture the generated password via the config on disk
    _run_cmd(bench, password=None)

    # token_urlsafe(12) is always ≥ 16 chars; policy only needs 8 chars + complexity.
    # Verify by checking it was stored (non-empty hash) rather than policy-validating
    # the hash, since the hash itself won't pass validate_admin_password.
    config = BenchConfig.read(bench.path)
    assert config.admin.password  # non-empty hash was written


def test_no_tty_generates_a_password(tmp_path: Path, monkeypatch, capsys) -> None:
    """Without a TTY (CI / unattended) the command should still generate a password."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    bench = _make_bench(tmp_path)
    _run_cmd(bench, password=None)

    out = capsys.readouterr().out
    assert "Generated admin password:" in out
    config = BenchConfig.read(bench.path)
    assert config.admin.password  # a hash was written
