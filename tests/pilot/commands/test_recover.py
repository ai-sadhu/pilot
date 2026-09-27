from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config import BenchConfig, S3Config, SiteConfig
from pilot.core.bench import Bench
from pilot.core.site import Site
from pilot.exceptions import BenchError


def _setup_bench(tmp_path: Path) -> Bench:
    bench_dir = tmp_path / "bench"
    bench_dir.mkdir()
    sites_dir = bench_dir / "sites"
    sites_dir.mkdir()

    config = BenchConfig(
        name="test-bench",
        s3=S3Config(
            bucket="my-bucket",
            endpoint_url="https://s3.example.com",
            access_key="key",
            secret_key="secret",
            region="us-east-1",
        ),
    )
    bench = Bench(config, bench_dir)
    return bench


def test_recover_command_unconfigured_s3_raises(tmp_path: Path) -> None:
    from pilot.commands.runtime.recover import RecoverCommand

    bench = _setup_bench(tmp_path)
    bench.config.s3 = S3Config(bucket="")

    cmd = RecoverCommand(bench=bench)
    with pytest.raises(BenchError, match="S3 offsite backups are not configured"):
        cmd.run()


def test_recover_command_no_sites_raises(tmp_path: Path) -> None:
    from pilot.commands.runtime.recover import RecoverCommand

    bench = _setup_bench(tmp_path)
    cmd = RecoverCommand(bench=bench)
    with pytest.raises(BenchError, match="No sites found"):
        cmd.run()


def test_recover_command_specific_site_runs_recovery(tmp_path: Path) -> None:
    from pilot.commands.runtime.recover import RecoverCommand

    bench = _setup_bench(tmp_path)
    site_dir = bench.sites_path / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text('{"maintenance_mode": 0, "pause_scheduler": 0}')

    cmd = RecoverCommand(bench=bench, site_name="site1.localhost")

    with patch("pilot.core.site.recovery.SiteRecovery.recover", return_value="20260927_140002") as mock_recover:
        cmd.run()
        mock_recover.assert_called_once_with(
            leave_maintenance=False,
            on_progress=cmd.report,
        )
