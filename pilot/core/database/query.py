from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pilot.core.database as database_module
from pilot.exceptions import DatabaseError


def substitute_site_macro(query: str, site_name: str) -> str:
    """Safely substitutes site macros without destructively changing unrelated SQL text details."""
    # Matches optional slash-escaped backticks, spaces, and macro token sequences specifically
    cleaned = re.sub(r"\\?\$\{\s*site\s*\}", site_name, query, flags=re.IGNORECASE)
    cleaned = re.sub(r"\\?\$site\b", site_name, cleaned, flags=re.IGNORECASE)
    return cleaned


def is_missing_table_error(message: str) -> bool:
    """Strictly evaluates if an error relates to a missing table, ignoring column mismatches."""
    low = message.lower()
    if "column" in low:
        return False
    return (
        "doesn't exist" in low
        or ("relation" in low and "does not exist" in low)
        or "no such table" in low
        or "1146" in low
    )


def get_active_bench_sites(bench_root: Path) -> list[str]:
    """Discovers and alpha-sorts active multi-tenant sites containing structural site configs."""
    sites_path = bench_root / "sites"
    if not sites_path.is_dir():
        raise FileNotFoundError("No sites found on this bench.")

    return sorted(
        d.name
        for d in sites_path.iterdir()
        if d.is_dir() and (d / "site_config.json").exists()
    )


def align_and_append_rows(
    all_columns: list[str],
    all_rows: list[list[Any]],
    result_columns: list[str],
    result_rows: list[list[Any]],
    site_name: str,
) -> list[str]:
    """Append rows from a site query, aligning by column names or rejecting incompatible sets."""
    if not all_columns:
        all_columns = list(result_columns)
        all_rows.extend(result_rows)
        return all_columns

    if list(result_columns) == all_columns:
        all_rows.extend(result_rows)
        return all_columns

    col_map = {col: i for i, col in enumerate(result_columns)}
    if set(result_columns) != set(all_columns):
        raise DatabaseError(
            f"Site '{site_name}' returned incompatible columns: {list(result_columns)} vs {all_columns}"
        )

    for row in result_rows:
        all_rows.append([row[col_map[c]] for c in all_columns])
    return all_columns


def execute_site_or_all_query(
    bench_root: Path | str,
    site: str,
    query: str,
    read_only: bool = True,
) -> dict[str, Any]:
    """Executes query for a single targeted site or orchestrates global bench aggregation."""
    bench_root = Path(bench_root)

    if site == "all":
        return _execute_all_sites_query(bench_root, query, read_only=read_only)

    site_query = substitute_site_macro(query, site)
    try:
        db = database_module.make_site_database(bench_root, site)
        result = db.execute(site_query, read_only=read_only)
    except (json.JSONDecodeError, KeyError, AttributeError) as exc:
        raise DatabaseError(f"Database configuration footprint failure: {exc!s}") from exc

    return {
        "columns": list(result.columns),
        "rows": list(result.rows),
        "row_count": len(result.rows),
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
        "affected_rows": result.affected_rows,
    }


def _execute_single_site_safely(
    bench_root: Path,
    site_name: str,
    query: str,
    read_only: bool,
    state: dict[str, Any],
    missing_table_errors: list[str],
    failed_sites: list[dict[str, str]],
) -> None:
    """Executes a query for a single site and aggregates metrics or categorizes database errors."""
    site_query = substitute_site_macro(query, site_name)

    try:
        try:
            db = database_module.make_site_database(bench_root, site_name)
            result = db.execute(site_query, read_only=read_only)
        except (json.JSONDecodeError, KeyError, AttributeError) as inner_exc:
            raise DatabaseError(f"Configuration profile layout crash: {inner_exc!s}") from inner_exc
    except DatabaseError as exc:
        err_msg = str(exc)
        if read_only and is_missing_table_error(err_msg):
            missing_table_errors.append(f"{site_name}: {err_msg}")
        else:
            failed_sites.append({"site": site_name, "error": err_msg})
        return

    state["columns"] = align_and_append_rows(
        state["columns"],
        state["rows"],
        list(result.columns),
        list(result.rows),
        site_name,
    )
    state["duration_ms"] += result.duration_ms
    if result.truncated:
        state["truncated"] = True
    if result.affected_rows is not None:
        state["affected_rows"] += result.affected_rows


def _execute_all_sites_query(bench_root: Path, query: str, read_only: bool) -> dict[str, Any]:
    """Internal runner helper to loop over all sites and aggregate matrix results safely."""
    site_dirs = get_active_bench_sites(bench_root)
    if not site_dirs:
        raise FileNotFoundError("No active sites discovered on this bench.")

    missing_table_errors: list[str] = []
    failed_sites: list[dict[str, str]] = []
    state: dict[str, Any] = {
        "columns": [],
        "rows": [],
        "duration_ms": 0.0,
        "truncated": False,
        "affected_rows": 0,
    }

    for site_name in site_dirs:
        _execute_single_site_safely(
            bench_root, site_name, query, read_only, state, missing_table_errors, failed_sites
        )

    if not state["columns"] and (len(missing_table_errors) + len(failed_sites) == len(site_dirs)):
        err = missing_table_errors[0] if missing_table_errors else failed_sites[0]["error"]
        raise DatabaseError(err)

    payload: dict[str, Any] = {
        "columns": state["columns"],
        "rows": state["rows"],
        "row_count": len(state["rows"]),
        "duration_ms": round(state["duration_ms"], 2),
        "truncated": state["truncated"],
        "affected_rows": state["affected_rows"],
    }
    if failed_sites:
        payload["failed_sites"] = failed_sites
    return payload
