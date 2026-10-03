"""Disaster Recovery: query, download, and restore offsite S3 backups."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.integrations.s3.backups import OffsiteBackup

if TYPE_CHECKING:
    from pilot.core.site import Site


class SiteRecovery:
    """Orchestrates offsite backup restoration for a single site."""

    def __init__(self, site: "Site") -> None:
        self.site = site

    def offsite(self) -> OffsiteBackup:
        if not self.site.bench.config.s3.is_configured:
            raise BenchError(
                f"S3 offsite backups are not configured for bench "
                f"'{self.site.bench.config.name or self.site.bench.path.name}'."
            )
        return OffsiteBackup.from_config(self.site.bench.config.s3, self.site.bench.path)

    def list_available_backups(self, limit: int | None = 10) -> dict[str, dict[str, str]]:
        """List available offsite backup runs for this site, newest first."""
        return self.offsite().list_backups(self.site.config.name, limit=limit)

    def resolve_target_backup(self, timestamp: str | None = None) -> tuple[str, dict[str, str]]:
        """Find the backup run for a specific timestamp, or the newest available."""
        client = self.offsite()
        site_name = self.site.config.name

        if timestamp:
            files = client.get_backup(site_name, timestamp)
            if not files:
                raise BenchError(
                    f"No offsite backup found for site '{site_name}' at timestamp '{timestamp}'."
                )
            return timestamp, files

        runs = client.list_backups(site_name, limit=1)
        if not runs:
            raise BenchError(f"No offsite backups found for site '{site_name}' in S3.")
        latest_ts, files = next(iter(runs.items()))
        return latest_ts, files

    def recover(
        self,
        timestamp: str | None = None,
        leave_maintenance: bool = False,
        on_progress: Callable[[str], None] = lambda _: None,
    ) -> str:
        """Pull the latest (or requested) offsite backup from S3 and restore in-place."""
        site_name = self.site.config.name
        on_progress(f"[{site_name}] Querying offsite S3 backups metadata...")
        target_ts, files = self.resolve_target_backup(timestamp)

        db_filename = files.get("database")
        if not db_filename:
            raise BenchError(
                f"Offsite backup '{target_ts}' for site '{site_name}' does not contain a database dump."
            )

        # Validate every artifact filename before use to prevent path traversal.
        for kind, filename in files.items():
            _validate_artifact_filename(filename, kind)

        # Capture the existing isolation state so we can restore it afterwards.
        prior_settings = self.site.maintenance_settings

        # 1. Enable maintenance mode to pause incoming traffic & background workers.
        on_progress(f"[{site_name}] Pausing traffic (enabling maintenance mode)...")
        self.site.set_maintenance_mode(True)

        # Use a separate temp dir so downloads never touch the site's own backup dir.
        with tempfile.TemporaryDirectory(prefix="pilot-recovery-") as tmp_str:
            tmp_dir = Path(tmp_str)
            client = self.offsite()

            try:
                db_path = self._download(client, site_name, target_ts, db_filename, tmp_dir, on_progress)
                public_path = self._maybe_download(client, site_name, target_ts, files.get("files"), tmp_dir, on_progress)
                private_path = self._maybe_download(client, site_name, target_ts, files.get("private_files"), tmp_dir, on_progress)
                if files.get("site_config"):
                    self._download(client, site_name, target_ts, files["site_config"], tmp_dir, on_progress)

                # 3. Restore database and public/private files.
                on_progress(f"[{site_name}] Restoring database and files from backup '{target_ts}'...")
                self.site.restore(
                    db_file=str(db_path),
                    public_files=str(public_path) if public_path else None,
                    private_files=str(private_path) if private_path else None,
                )

                # 4. Run schema migration & clear cache via Site directly.
                on_progress(f"[{site_name}] Running database migrations (bench migrate)...")
                self.site.migrate(skip_failing=False)

                on_progress(f"[{site_name}] Clearing Redis & site cache...")
                self.site.clear_cache()

            except Exception:
                # Restore pre-recovery isolation state before re-raising.
                self.site.set_maintenance_settings(prior_settings)
                raise
        # TemporaryDirectory context exit deletes the temp dir and all downloads.

        # 5. Restore the pre-recovery isolation state, or keep maintenance if requested.
        if leave_maintenance:
            on_progress(f"[{site_name}] Site left in maintenance mode as requested.")
        else:
            on_progress(f"[{site_name}] Restoring prior isolation state...")
            self.site.set_maintenance_settings(prior_settings)

        return target_ts

    def _download(
        self,
        client: OffsiteBackup,
        site_name: str,
        timestamp: str,
        filename: str,
        dest_dir: Path,
        on_progress: Callable[[str], None],
    ) -> Path:
        on_progress(f"[{site_name}] Downloading {filename}...")
        dest = dest_dir / filename
        client.download(site_name, timestamp, filename, dest)
        return dest

    def _maybe_download(
        self,
        client: OffsiteBackup,
        site_name: str,
        timestamp: str,
        filename: str | None,
        dest_dir: Path,
        on_progress: Callable[[str], None],
    ) -> Path | None:
        if not filename:
            return None
        return self._download(client, site_name, timestamp, filename, dest_dir, on_progress)


def _validate_artifact_filename(filename: str, kind: str) -> None:
    """Raise BenchError if filename would escape any download directory."""
    if (
        not filename
        or filename.startswith("/")
        or filename.startswith(".")
        or "/" in filename
        or "\\" in filename
    ):
        raise BenchError(
            f"Offsite backup metadata contains an unsafe {kind} filename: {filename!r}."
        )
