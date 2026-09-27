import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pilot.config import BenchConfig, S3Config, SiteConfig
from pilot.core.site import Site
from pilot.core.site.recovery import SiteRecovery
from pilot.exceptions import BenchError


def _setup_bench_and_site(tmp_path: Path):
    bench_dir = tmp_path / "test-bench"
    sites_dir = bench_dir / "sites"
    site_dir = sites_dir / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"maintenance_mode": 0, "pause_scheduler": 0}))

    bench_config = BenchConfig(
        name="test-bench",
        s3=S3Config(
            bucket="test-bucket",
            endpoint_url="https://s3.example.com",
            access_key="test-key",
            secret_key="test-secret",
            region="us-east-1",
        ),
    )
    bench = SimpleNamespace(
        path=bench_dir,
        sites_path=sites_dir,
        config=bench_config,
    )
    site = Site(SiteConfig(name="site1.localhost", apps=[]), bench)
    return bench, site


def test_recovery_unconfigured_s3_raises(tmp_path: Path) -> None:
    bench, site = _setup_bench_and_site(tmp_path)
    bench.config.s3 = S3Config(bucket="")  # unconfigured

    recovery = SiteRecovery(site)
    with pytest.raises(BenchError, match="S3 offsite backups are not configured"):
        recovery.recover()


def test_recovery_no_backups_found_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench, site = _setup_bench_and_site(tmp_path)

    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {}
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    recovery = SiteRecovery(site)
    with pytest.raises(BenchError, match="No offsite backups found"):
        recovery.recover()


def test_recovery_specific_timestamp_not_found_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench, site = _setup_bench_and_site(tmp_path)

    fake_offsite = MagicMock()
    fake_offsite.get_backup.return_value = None
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    recovery = SiteRecovery(site)
    with pytest.raises(BenchError, match="No offsite backup found for site 'site1.localhost' at timestamp '20260101_000000'"):
        recovery.recover(timestamp="20260101_000000")


def test_successful_recovery_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench, site = _setup_bench_and_site(tmp_path)
    ts = "20260927_140002"

    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {
        ts: {
            "database": f"{ts}-site1.localhost-database.sql.gz",
            "files": f"{ts}-site1.localhost-files.tar",
            "private_files": f"{ts}-site1.localhost-private-files.tar",
            "site_config": f"{ts}-site1.localhost-site_config_backup.json",
        }
    }

    # Simulate S3 download by creating fake files when download is called
    def fake_download(site_name, timestamp, filename, dest):
        dest.write_text("dummy-backup-data")

    fake_offsite.download.side_effect = fake_download
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    # Mock site restore, migrate, clear_cache
    site.restore = MagicMock()
    site.commands = SimpleNamespace(
        migrate=MagicMock(),
        clear_cache=MagicMock(),
    )

    progress_messages = []
    restored_ts = site.recover(on_progress=lambda msg: progress_messages.append(msg))

    assert restored_ts == ts
    site.restore.assert_called_once()
    site.commands.migrate.assert_called_once_with(skip_failing=False)
    site.commands.clear_cache.assert_called_once()

    # Maintenance mode should be restored to False
    assert site.maintenance_mode is False

    # Downloaded archives should be cleaned up
    backups_dir = site.path / "private" / "backups"
    assert not (backups_dir / f"{ts}-site1.localhost-database.sql.gz").exists()
    assert not (backups_dir / f"{ts}-site1.localhost-site_config_backup.json").exists()


def test_recovery_leave_maintenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench, site = _setup_bench_and_site(tmp_path)
    ts = "20260927_140002"

    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {
        ts: {
            "database": f"{ts}-site1.localhost-database.sql.gz",
        }
    }
    fake_offsite.download.side_effect = lambda site_name, timestamp, filename, dest: dest.write_text("dummy")
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    site.restore = MagicMock()
    site.commands = SimpleNamespace(migrate=MagicMock(), clear_cache=MagicMock())

    site.recover(leave_maintenance=True)
    assert site.maintenance_mode is True
