from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


def run_cleanup(
    *,
    report_date: date,
    output_dir: Path,
    root: Path,
    settings: dict[str, Any],
) -> list[str]:
    cleanup_settings = settings.get("cleanup", {})
    if not cleanup_settings.get("enabled", True):
        return ["Cleanup skipped: disabled in settings."]

    messages: list[str] = []
    report_keep_days = int(cleanup_settings.get("keep_full_reports_days", 7))
    filing_keep_days = int(cleanup_settings.get("keep_filing_cache_days", 2))
    bhavcopy_keep_days = int(cleanup_settings.get("keep_bhavcopy_cache_days", 120))

    messages.extend(_cleanup_reports(output_dir, report_date, keep_days=report_keep_days))
    messages.extend(_cleanup_by_mtime(root / "data" / "filing_cache", keep_days=filing_keep_days, label="filing cache"))
    messages.extend(_cleanup_bhavcopy_cache(root / "data" / "cache", report_date, keep_days=bhavcopy_keep_days))
    return messages


def _cleanup_reports(output_dir: Path, report_date: date, *, keep_days: int) -> list[str]:
    if not output_dir.exists():
        return []
    cutoff = report_date - timedelta(days=keep_days)
    deleted = 0
    for path in output_dir.glob("stock_report_*.md"):
        report_day = _report_date(path)
        if not report_day or report_day >= cutoff:
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError:
            continue
    return [f"Cleanup: deleted {deleted} full report(s) older than {keep_days} days; daily summaries kept."]


def _cleanup_by_mtime(path: Path, *, keep_days: int, label: str) -> list[str]:
    if not path.exists():
        return []
    cutoff = datetime.now().timestamp() - keep_days * 86400
    deleted = 0
    bytes_deleted = 0
    for child in path.iterdir():
        if not child.is_file():
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        if stat.st_mtime >= cutoff:
            continue
        try:
            bytes_deleted += stat.st_size
            child.unlink()
            deleted += 1
        except OSError:
            continue
    mb = bytes_deleted / (1024 * 1024)
    return [f"Cleanup: deleted {deleted} {label} file(s), freed {mb:.1f} MB."]


def _cleanup_bhavcopy_cache(path: Path, report_date: date, *, keep_days: int) -> list[str]:
    if not path.exists():
        return []
    cutoff = report_date - timedelta(days=keep_days)
    deleted = 0
    for child in path.iterdir():
        if not child.is_file():
            continue
        cache_day = _cache_date(child)
        if not cache_day or cache_day >= cutoff:
            continue
        try:
            child.unlink()
            deleted += 1
        except OSError:
            continue
    return [f"Cleanup: deleted {deleted} market data cache file(s) older than {keep_days} days."]


def _report_date(path: Path) -> date | None:
    try:
        value = path.stem.replace("stock_report_", "")
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _cache_date(path: Path) -> date | None:
    name = path.name
    for fmt, prefix in [("%Y%m%d", "bhavcopy_cm_"), ("%d%m%Y", "ind_close_all_")]:
        if not name.startswith(prefix):
            continue
        raw = name.replace(prefix, "").split(".", 1)[0]
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            return None
    return None
