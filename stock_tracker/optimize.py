from __future__ import annotations

import argparse
import csv
import os
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .backtest import run_backdated_test
from .main import main as run_main  # uses env vars and config


@dataclass(frozen=True)
class ParamSet:
    calendar_only_multiplier: float
    calendar_only_confidence_cap: float
    top_overall_event_only_min_total: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parameter sweep optimizer for stock_tracker")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument(
        "--step-days",
        type=int,
        default=7,
        help="Run every N days (default 7). Use 1 for daily but it can be slow.",
    )
    parser.add_argument(
        "--recent-daily-days",
        type=int,
        default=14,
        help="Additionally run daily for the last N days in the range (default 14). Set 0 to disable.",
    )
    parser.add_argument("--max-per-day", type=int, default=10, help="Max picks per day (Top Overall)")
    parser.add_argument("--horizons", default="30,60,90", help="Forward days (default 30,60,90)")
    parser.add_argument("--out-dir", default="reports/opt_runs", help="Output directory")
    parser.add_argument(
        "--grid",
        default="medium",
        choices=["small", "medium", "large"],
        help="Parameter grid size (default medium).",
    )
    parser.add_argument(
        "--objective",
        default="mean_alpha_midcap",
        choices=[
            "mean_return",
            "median_return",
            "mean_alpha_midcap",
            "mean_alpha_midcap_21td",
            "mean_alpha_midcap_63td",
            "mean_alpha_midcap_126td",
            "p05_return",
        ],
        help="Sort leaderboard by this metric (default mean_alpha_midcap).",
    )
    return parser.parse_args()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _date_range(start: date, end: date, step_days: int) -> list[date]:
    dates: list[date] = []
    d = start
    while d <= end:
        dates.append(d)
        d = d + timedelta(days=step_days)
    return dates


def _weekly_plus_recent_daily(start: date, end: date, step_days: int, recent_daily_days: int) -> list[date]:
    dates = set(_date_range(start, end, step_days))
    if recent_daily_days > 0:
        recent_start = max(start, end - timedelta(days=recent_daily_days - 1))
        d = recent_start
        while d <= end:
            dates.add(d)
            d = d + timedelta(days=1)
    return sorted(dates)


def _run_reports_for_dates(dates: list[date], *, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # We call stock_tracker.main in-process by patching sys.argv.
    import sys

    original_argv = sys.argv[:]
    try:
        for d in dates:
            sys.argv = ["stock_tracker.main", "--date", d.isoformat(), "--output-dir", str(out_dir)]
            run_main()
    finally:
        sys.argv = original_argv


def _compute_metrics(csv_path: Path) -> dict[str, float]:
    rows = list(csv.DictReader(csv_path.open("r", newline="", encoding="utf-8")))
    returns: list[float] = []
    alpha_mid: list[float] = []
    alpha_63td: list[float] = []
    alpha_126td: list[float] = []
    alpha_21td: list[float] = []
    for row in rows:
        if row.get("return_to_latest_pct"):
            returns.append(float(row["return_to_latest_pct"]))
        if row.get("alpha_vs_midcap_to_latest_pct"):
            alpha_mid.append(float(row["alpha_vs_midcap_to_latest_pct"]))
        if row.get("alpha_vs_midcap_plus_63td_pct"):
            alpha_63td.append(float(row["alpha_vs_midcap_plus_63td_pct"]))
        if row.get("alpha_vs_midcap_plus_126td_pct"):
            alpha_126td.append(float(row["alpha_vs_midcap_plus_126td_pct"]))
        if row.get("alpha_vs_midcap_plus_21td_pct"):
            alpha_21td.append(float(row["alpha_vs_midcap_plus_21td_pct"]))
    if not returns:
        return {
            "rows": float(len(rows)),
            "valid_returns": 0.0,
            "win_rate": 0.0,
            "mean_return": 0.0,
            "median_return": 0.0,
            "mean_alpha_midcap": 0.0,
            "p05_return": 0.0,
            "p95_return": 0.0,
            "mean_alpha_midcap_63td": 0.0,
            "mean_alpha_midcap_126td": 0.0,
            "mean_alpha_midcap_21td": 0.0,
        }
    pos = sum(1 for r in returns if r > 0)
    mean_alpha = statistics.mean(alpha_mid) if alpha_mid else 0.0
    mean_alpha_63 = statistics.mean(alpha_63td) if alpha_63td else 0.0
    mean_alpha_126 = statistics.mean(alpha_126td) if alpha_126td else 0.0
    mean_alpha_21 = statistics.mean(alpha_21td) if alpha_21td else 0.0
    s = sorted(returns)
    p05 = s[max(0, int(len(s) * 0.05) - 1)]
    p95 = s[min(len(s) - 1, int(len(s) * 0.95) - 1)]
    return {
        "rows": float(len(rows)),
        "valid_returns": float(len(returns)),
        "win_rate": pos / len(returns),
        "mean_return": statistics.mean(returns),
        "median_return": statistics.median(returns),
        "mean_alpha_midcap": mean_alpha,
        "p05_return": p05,
        "p95_return": p95,
        "mean_alpha_midcap_63td": mean_alpha_63,
        "mean_alpha_midcap_126td": mean_alpha_126,
        "mean_alpha_midcap_21td": mean_alpha_21,
    }


def _build_param_sets(grid: str) -> list[ParamSet]:
    base = [
        ParamSet(0.15, 0.45, 2.0),
        ParamSet(0.20, 0.50, 2.5),
        ParamSet(0.30, 0.55, 2.5),
        ParamSet(0.35, 0.55, 3.0),
        ParamSet(0.45, 0.60, 3.0),
        ParamSet(0.50, 0.60, 3.5),
        ParamSet(0.60, 0.65, 3.5),
    ]
    if grid == "small":
        return base[:3]
    if grid == "medium":
        # Curated 12 set grid (keeps runtime sane but explores range).
        return [
            ParamSet(0.10, 0.45, 2.0),
            ParamSet(0.15, 0.45, 2.0),
            ParamSet(0.20, 0.50, 2.0),
            ParamSet(0.20, 0.50, 2.5),
            ParamSet(0.25, 0.50, 2.5),
            ParamSet(0.30, 0.55, 2.5),
            ParamSet(0.35, 0.55, 2.5),
            ParamSet(0.35, 0.55, 3.0),
            ParamSet(0.40, 0.60, 3.0),
            ParamSet(0.45, 0.60, 3.0),
            ParamSet(0.50, 0.60, 3.5),
            ParamSet(0.60, 0.65, 4.0),
        ]
    # large
    return [
        ParamSet(0.05, 0.45, 2.0),
        ParamSet(0.10, 0.45, 2.0),
        ParamSet(0.15, 0.45, 2.0),
        ParamSet(0.20, 0.50, 2.0),
        ParamSet(0.25, 0.50, 2.0),
        ParamSet(0.30, 0.50, 2.0),
        ParamSet(0.35, 0.55, 2.0),
        ParamSet(0.40, 0.55, 2.5),
        ParamSet(0.45, 0.55, 2.5),
        ParamSet(0.50, 0.60, 2.5),
        ParamSet(0.55, 0.60, 3.0),
        ParamSet(0.60, 0.65, 3.0),
        ParamSet(0.65, 0.65, 3.5),
        ParamSet(0.70, 0.70, 3.5),
        ParamSet(0.75, 0.70, 4.0),
    ]


def main() -> int:
    args = parse_args()
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    horizons = [int(part.strip()) for part in str(args.horizons).split(",") if part.strip()]
    out_root = Path(args.out_dir)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    dates = _weekly_plus_recent_daily(start, end, args.step_days, args.recent_daily_days)

    param_sets = _build_param_sets(args.grid)

    leaderboard_path = run_dir / "leaderboard.csv"
    with leaderboard_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "calendar_only_multiplier",
                "calendar_only_confidence_cap",
                "top_overall_event_only_min_total",
                "rows",
                "valid_returns",
                "win_rate",
                "mean_return",
                "median_return",
                "mean_alpha_midcap",
                "p05_return",
                "p95_return",
                "mean_alpha_midcap_21td",
                "mean_alpha_midcap_63td",
                "mean_alpha_midcap_126td",
                "csv_path",
            ],
        )
        writer.writeheader()

        leaderboard_rows: list[dict[str, object]] = []
        for idx, params in enumerate(param_sets, start=1):
            os.environ["CALENDAR_ONLY_MULTIPLIER"] = str(params.calendar_only_multiplier)
            os.environ["CALENDAR_ONLY_CONFIDENCE_CAP"] = str(params.calendar_only_confidence_cap)
            os.environ["TOP_OVERALL_EVENT_ONLY_MIN_TOTAL"] = str(params.top_overall_event_only_min_total)

            md_dir = run_dir / f"set_{idx:02d}_md"
            _run_reports_for_dates(dates, out_dir=md_dir)

            csv_path = run_dir / f"set_{idx:02d}_backtest.csv"
            run_backdated_test(
                start=start,
                end=end,
                horizons=horizons,
                max_per_day=args.max_per_day,
                report_dir=md_dir,
                out_path=csv_path,
            )

            metrics = _compute_metrics(csv_path)
            row = {
                "calendar_only_multiplier": params.calendar_only_multiplier,
                "calendar_only_confidence_cap": params.calendar_only_confidence_cap,
                "top_overall_event_only_min_total": params.top_overall_event_only_min_total,
                "rows": int(metrics["rows"]),
                "valid_returns": int(metrics["valid_returns"]),
                "win_rate": round(metrics["win_rate"], 4),
                "mean_return": round(metrics["mean_return"], 4),
                "median_return": round(metrics["median_return"], 4),
                "mean_alpha_midcap": round(metrics["mean_alpha_midcap"], 4),
                "p05_return": round(metrics["p05_return"], 4),
                "p95_return": round(metrics["p95_return"], 4),
                "mean_alpha_midcap_21td": round(metrics["mean_alpha_midcap_21td"], 4),
                "mean_alpha_midcap_63td": round(metrics["mean_alpha_midcap_63td"], 4),
                "mean_alpha_midcap_126td": round(metrics["mean_alpha_midcap_126td"], 4),
                "csv_path": str(csv_path),
            }
            leaderboard_rows.append(row)

        key = args.objective
        leaderboard_rows.sort(key=lambda r: float(r.get(key) or 0.0), reverse=True)
        for row in leaderboard_rows:
            writer.writerow(row)

    print(f"Optimizer run written: {run_dir}")
    print(f"Leaderboard: {leaderboard_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
