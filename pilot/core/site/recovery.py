"""Disaster Recovery: query, download, and restore offsite S3 backups."""

from __future__ import annotations

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
                f"S3 offsite backups are not configured for bench '{self.site.bench.config.name or self.site.bench.path.name}'."
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

        # 1. Enable maintenance mode to pause incoming traffic & background workers
        on_progress(f"[{site_name}] Pausing traffic (enabling maintenance mode)...")
        self.site.set_maintenance_mode(True)

        backup_dir = self.site.path / "private" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        downloaded_paths: list[Path] = []
        client = self.offsite()

        try:
            # 2. Download all available backup artifacts from S3
            on_progress(f"[{site_name}] Downloading database dump ({db_filename})...")
            db_path = backup_dir / db_filename
            client.download(site_name, target_ts, db_filename, db_path)
            downloaded_paths.append(db_path)

            public_files_name = files.get("files")
            public_path: Path | None = None
            if public_files_name:
                on_progress(f"[{site_name}] Downloading public files ({public_files_name})...")
                public_path = backup_dir / public_files_name
                client.download(site_name, target_ts, public_files_name, public_path)
                downloaded_paths.append(public_path)

            private_files_name = files.get("private_files")
            private_path: Path | None = None
            if private_files_name:
                on_progress(f"[{site_name}] Downloading private files ({private_files_name})...")
                private_path = backup_dir / private_files_name
                client.download(site_name, target_ts, private_files_name, private_path)
                downloaded_paths.append(private_path)

            site_config_name = files.get("site_config")
            if site_config_name:
                on_progress(f"[{site_name}] Downloading site config backup ({site_config_name})...")
                site_config_path = backup_dir / site_config_name
                client.download(site_name, target_ts, site_config_name, site_config_path)
                downloaded_paths.append(site_config_path)

            # 3. Restore database and public/private files
            on_progress(f"[{site_name}] Restoring database and files from backup '{target_ts}'...")
            self.site.restore(
                db_file=str(db_path),
                public_files=str(public_path) if public_path else None,
                private_files=str(private_path) if private_path else None,
            )

            # 4. Run schema migration & clear cache
            on_progress(f"[{site_name}] Running database migrations (bench migrate)...")
            self.site.commands.migrate(skip_failing=False)

            on_progress(f"[{site_name}] Clearing Redis & site cache...")
            self.site.commands.clear_cache()

        finally:
            # 5. Clean up downloaded archive files
            for path in downloaded_paths:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass

        # 6. Restore or keep maintenance mode
        if not leave_maintenance:
            on_progress(f"[{site_name}] Resuming traffic (disabling maintenance mode)...")
            self.site.set_maintenance_mode(False)
        else:
            on_progress(f"[{site_name}] Site left in maintenance mode as requested.")

        return target_ts
