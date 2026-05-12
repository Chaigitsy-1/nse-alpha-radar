from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from .config import load_config, load_env_file
from .delivery import send_email, send_telegram
from .lifecycle import build_capex_lifecycle_signals
from .report import (
    _action_for_score,
    _actionable_scores,
    _quality_tier_for_score,
    _watchlist_scores,
    _weighted_signal,
    write_markdown_report,
)
from .scoring import score_companies
from .signals import classify_fundamentals, classify_price_volume, classify_source_items, classify_trend_momentum
from .sources import (
    load_bse_rss,
    load_manual_events,
    load_nse_index_closes,
    load_nse_corporate_items_for_date,
    load_nse_market_snapshot_for_date,
    load_bhavcopy_trend_rows,
    load_bhavcopy_risk_reward,
    load_optional_csv,
)
from .universe import load_universe_with_warnings
from .universe import load_market_cap_universe_with_warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Indian midcap/smallcap opportunity tracker")
    parser.add_argument("--date", default="today", help="Report date: today or YYYY-MM-DD")
    parser.add_argument("--send", action="store_true", help="Send report via configured email/Telegram")
    parser.add_argument("--output-dir", default="", help="Override output directory for reports")
    parser.add_argument(
        "--universe",
        choices=["index", "marketcap"],
        default="index",
        help="Universe mode: index uses Midcap 150 + Smallcap 250; marketcap uses NSE market-cap file.",
    )
    parser.add_argument("--min-market-cap-cr", type=float, default=3000.0, help="Minimum market cap in INR crore.")
    parser.add_argument("--max-market-cap-cr", type=float, default=50000.0, help="Maximum market cap in INR crore.")
    return parser.parse_args()


def parse_report_date(value: str) -> date:
    if value.lower() == "today":
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> int:
    args = parse_args()
    report_date = parse_report_date(args.date)
    load_env_file()
    config = load_config()

    source_errors: list[str] = []
    if args.universe == "marketcap":
        companies, universe_warnings = load_market_cap_universe_with_warnings(
            config,
            min_market_cap_cr=args.min_market_cap_cr,
            max_market_cap_cr=args.max_market_cap_cr,
        )
    else:
        companies, universe_warnings = load_universe_with_warnings(config)
    source_errors.extend(universe_warnings)
    nse_items, nse_errors = load_nse_corporate_items_for_date(config, report_date)
    source_errors.extend(nse_errors)
    nse_market_rows, nse_market_errors = load_nse_market_snapshot_for_date(config, report_date)
    source_errors.extend(nse_market_errors)
    bse_items, bse_errors = load_bse_rss(config)
    source_errors.extend(bse_errors)
    manual_items = load_manual_events(config)
    price_volume_rows = load_optional_csv(config, "price_volume")
    fundamental_rows = load_optional_csv(config, "fundamentals")
    index_stats, index_errors = load_nse_index_closes(config, report_date)
    source_errors.extend(index_errors)
    trend_rows, trend_errors = load_bhavcopy_trend_rows(report_date, set(companies.keys()))
    source_errors.extend(trend_errors)
    output_dir = Path(args.output_dir) if args.output_dir else (config.root / "reports")

    signals = []
    signals.extend(classify_source_items(config, companies, nse_items + bse_items + manual_items))
    signals.extend(classify_price_volume(config, companies, nse_market_rows + price_volume_rows))
    signals.extend(classify_trend_momentum(config, companies, trend_rows))
    signals.extend(classify_fundamentals(config, companies, fundamental_rows))
    signals.extend(build_capex_lifecycle_signals(signals, report_date=report_date, history_dir=output_dir))

    scores = score_companies(companies, signals)
    risk_reward, risk_reward_errors = load_bhavcopy_risk_reward(report_date, {score.symbol for score in scores})
    source_errors.extend(risk_reward_errors)
    for score in scores:
        score.risk_reward = risk_reward.get(score.symbol)
    report_path = write_markdown_report(
        report_date=report_date,
        scores=scores,
        output_dir=output_dir,
        source_errors=source_errors,
        index_stats=index_stats,
        max_items=config.settings["report"]["max_items_per_section"],
    )

    print(f"Report written: {report_path}")
    print(f"Universe companies: {len(companies)}")
    print(f"Signals captured: {len(signals)}")
    if source_errors:
        print("Source warnings:")
        for error in source_errors[:5]:
            print(f"- {error}")

    if args.send:
        body = _summary_body(report_date, scores, report_path)
        email_ok, email_status = _safe_send_email(
            subject=f"Indian Stock Tracker - {report_date.isoformat()}",
            body=body,
            attachment=report_path,
        )
        print(email_status)
        telegram_ok, telegram_status = _safe_send_telegram(body)
        print(telegram_status)
        if not email_ok or not telegram_ok:
            print("Delivery is partially configured. Report was still generated locally.")

    return 0


def _safe_send_email(subject: str, body: str, attachment):
    try:
        return send_email(subject=subject, body=body, attachment=attachment)
    except Exception as exc:
        return False, f"Email skipped: delivery attempt failed ({exc})."


def _safe_send_telegram(body: str):
    try:
        return send_telegram(body)
    except Exception as exc:
        return False, f"Telegram skipped: delivery attempt failed ({exc})."


def _summary_body(report_date: date, scores, report_path) -> str:
    actionable = _actionable_scores(scores)
    watchlist = _watchlist_scores(scores)
    lines = [
        "Indian Stock Tracker",
        f"Date: {report_date.isoformat()}",
        "Universe: NSE market-cap Rs 3,000-50,000 cr",
        "",
        f"Actionable today: {len(actionable)}",
        f"High-quality watchlist: {len(watchlist)}",
    ]

    lines.extend(["", "ACTIONABLE TODAY", "----------------"])
    if actionable:
        lines.extend(_mobile_score_cards(actionable, limit=5))
    else:
        lines.append("No strict actionable setup today.")

    lines.extend(["", "HIGH-QUALITY WATCHLIST", "----------------------"])
    if watchlist:
        lines.extend(_mobile_score_cards(watchlist, limit=7))
        if len(watchlist) > 7:
            lines.append(f"+ {len(watchlist) - 7} more in the full report.")
    else:
        lines.append("No high-quality watchlist names today.")

    lines.extend(["", f"Full report: {report_path}"])
    return "\n".join(lines)


def _mobile_score_cards(scores, limit: int) -> list[str]:
    lines: list[str] = []
    for rank, score in enumerate(scores[:limit], start=1):
        tier, tier_reason = _quality_tier_for_score(score)
        action, action_reason = _action_for_score(score)
        lines.extend(
            [
                "",
                f"{rank}. {score.symbol} - {score.company_name}",
                f"Grade: {tier} ({tier_reason})",
                f"Action: {action}",
                f"Score: {score.total:.2f} | Risk: {score.risk:.2f}",
                f"R/R: {_compact_risk_reward(score)}",
                f"Why: {_compact_why(score)}",
                f"Next: {_compact_next_step(action_reason)}",
            ]
        )
    return lines


def _compact_risk_reward(score) -> str:
    rr = getattr(score, "risk_reward", None)
    if rr is None:
        return "unavailable"
    return (
        f"{rr.verdict}; CMP {rr.cmp:.2f}; stop {rr.stop_loss:.2f}; "
        f"target {rr.target:.2f}; R/R {rr.reward_risk:.2f}x"
    )


def _compact_next_step(action_reason: str) -> str:
    if "confirmation" in action_reason:
        return "Open filing, verify result/guidance, then check entry risk."
    if "momentum" in action_reason or "price" in action_reason:
        return "Check chart context; avoid chasing a vertical move."
    if "risk" in action_reason:
        return "Read risk filing first; do not treat as clean setup."
    return "Validate filing, numbers, and management commentary."


def _compact_why(score) -> str:
    top = sorted(score.signals, key=_weighted_signal, reverse=True)[:2]
    if not top:
        return "No strong signal"
    parts = []
    for signal in top:
        label = signal.label.replace("|", "/").strip()
        if len(label) > 48:
            label = label[:45].rstrip() + "..."
        parts.append(f"{signal.category}: {label}")
    return "; ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
