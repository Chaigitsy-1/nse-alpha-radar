from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from .main import main as run_main


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay report generation over a date range")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--step-days", type=int, default=7, help="Generate every N calendar days (default 7)")
    parser.add_argument(
        "--recent-daily-days",
        type=int,
        default=30,
        help="Additionally generate daily for last N days of range (default 30). Set 0 to disable.",
    )
    parser.add_argument("--out-dir", default="reports/replay", help="Output directory for markdown reports")
    return parser.parse_args()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _weekly_plus_recent_daily(start: date, end: date, step_days: int, recent_daily_days: int) -> list[date]:
    dates = set()
    d = start
    while d <= end:
        dates.add(d)
        d = d + timedelta(days=step_days)
    if recent_daily_days > 0:
        recent_start = max(start, end - timedelta(days=recent_daily_days - 1))
        d = recent_start
        while d <= end:
            dates.add(d)
            d = d + timedelta(days=1)
    return sorted(dates)


def main() -> int:
    args = parse_args()
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dates = _weekly_plus_recent_daily(start, end, args.step_days, args.recent_daily_days)

    import sys

    original_argv = sys.argv[:]
    try:
        for d in dates:
            sys.argv = ["stock_tracker.main", "--date", d.isoformat(), "--output-dir", str(out_dir)]
            run_main()
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

