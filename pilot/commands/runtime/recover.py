from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class RecoverCommand(Command):
    name: ClassVar[str] = "recover"
    help: ClassVar[str] = "Disaster Recovery: pull the latest offsite S3 backup and restore bench sites."
    bench_mode: ClassVar[BenchMode] = BenchMode.AUTO
    supports_all_benches: ClassVar[bool] = True

    site_name: Annotated[
        str | None,
        Arg(
            help="Specific site name to recover (defaults to all sites in bench).",
            short="-s",
            metavar="site",
        ),
    ] = None
    leave_maintenance: Annotated[
        bool,
        Arg(help="Keep site in maintenance mode after recovery completes."),
    ] = False

    def run(self) -> None:
        from pilot.core.site.recovery import SiteRecovery

        bench_label = self.bench.config.name or self.bench.path.name

        if not self.bench.config.s3.is_configured:
            raise BenchError(
                f"S3 offsite backups are not configured in bench.toml for '{bench_label}'."
            )

        sites = [self.bench.site(self.site_name)] if self.site_name else self.bench.sites()
        if not sites:
            raise BenchError(
                f"No sites found in bench '{bench_label}'."
                if not self.site_name
                else f"Site '{self.site_name}' does not exist in bench '{bench_label}'."
            )

        successful: list[tuple[str, str]] = []
        failed: list[tuple[str, str]] = []

        self.report(f"🚀 Starting Disaster Recovery for bench '{bench_label}'...")

        for site in sites:
            site_name = site.config.name
            self.report(f"\n--- Recovering {site_name} ---")
            recovery = SiteRecovery(site)
            try:
                restored_ts = recovery.recover(
                    leave_maintenance=self.leave_maintenance,
                    on_progress=self.report,
                )
                successful.append((site_name, restored_ts))
                self.report(f"✅ Successfully recovered site '{site_name}' (backup: {restored_ts}).")
            except Exception as exc:
                failed.append((site_name, str(exc)))
                self.report(f"❌ Failed to recover site '{site_name}': {exc}")

        self.report("\n==================== Recovery Summary ====================")
        for s_name, ts in successful:
            self.report(f"  ✅ {s_name}: Restored snapshot {ts}")
        for f_name, err in failed:
            self.report(f"  ❌ {f_name}: {err}")

        if failed:
            raise BenchError(f"Disaster Recovery failed for: {', '.join(f[0] for f in failed)}")
        else:
            self.report(f"\n🎉 Disaster Recovery completed successfully for bench '{bench_label}'!")
