from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

from .ai_validation import build_ai_validation_signals
from .config import load_config, load_env_file
from .cleanup import run_cleanup
from .delivery import send_email, send_telegram
from .filing_validation import build_filing_validation_signals
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
    validation_signals, validation_errors = build_filing_validation_signals(
        companies,
        nse_items,
        cache_dir=config.root / "data" / "filing_cache",
    )
    source_errors.extend(validation_errors)
    signals.extend(validation_signals)
    ai_validation_signals, ai_validation_errors = build_ai_validation_signals(
        companies,
        nse_items,
        cache_dir=config.root / "data" / "filing_cache",
        queue_dir=config.root / "data" / "ai_validation_queue",
        results_dir=config.root / "data" / "ai_validation_results",
        report_date=report_date,
    )
    source_errors.extend(ai_validation_errors)
    signals.extend(ai_validation_signals)
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
    cleanup_messages = run_cleanup(
        report_date=report_date,
        output_dir=output_dir,
        root=config.root,
        settings=config.settings,
    )

    print(f"Report written: {report_path}")
    print(f"Universe companies: {len(companies)}")
    print(f"Signals captured: {len(signals)}")
    for message in cleanup_messages:
        print(message)
    if source_errors:
        print("Source warnings:")
        for error in source_errors[:5]:
            print(f"- {error}")

    if args.send:
        body = _summary_body(report_date, scores, report_path, source_errors)
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


def _summary_body(report_date: date, scores, report_path, source_errors: list[str] | None = None) -> str:
    actionable = _actionable_scores(scores)
    watchlist = _watchlist_scores(scores)
    validation_status = _telegram_validation_status(scores, source_errors or [])
    lines = [
        "NSE Alpha Radar",
        f"Date: {report_date.isoformat()}",
        "Universe: NSE market-cap Rs 3,000-50,000 cr",
        f"Validation: {validation_status}",
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
                f"Validation: {_compact_validation(score)}",
                f"Found: {_compact_found_reason(score)}",
                f"Signal: {_compact_why(score)}",
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


def _telegram_validation_status(scores, source_errors: list[str]) -> str:
    ai_merged = _first_warning_value(source_errors, "AI validation merged")
    ai_queued = _first_warning_value(source_errors, "AI validation queued")
    ai_capped = _first_warning_value(source_errors, "AI validation candidate scan capped")
    has_ai_signal = any(signal.category == "ai_validation" for score in scores for signal in score.signals)
    has_ai_risk = any(
        signal.category == "red_flag" and "ai validation" in (signal.evidence or "").lower()
        for score in scores
        for signal in score.signals
    )
    has_local = any(signal.category == "filing_validation" for score in scores for signal in score.signals)

    parts: list[str] = []
    if ai_merged:
        parts.append(ai_merged.replace("AI validation ", "AI "))
    elif ai_queued:
        parts.append(ai_queued.replace("AI validation ", "AI "))
    elif has_ai_signal or has_ai_risk:
        parts.append("AI results active")
    else:
        parts.append("AI none")

    if ai_capped:
        parts.append("top filings capped")
    if has_local:
        parts.append("local fallback active")
    else:
        parts.append("metadata fallback only")
    return "; ".join(parts)


def _first_warning_value(source_errors: list[str], prefix: str) -> str:
    for error in source_errors:
        if error.startswith(prefix):
            return error.rstrip(".")
    return ""


def _compact_validation(score) -> str:
    signals = sorted(score.signals, key=_weighted_signal, reverse=True)
    ai_positive = [signal for signal in signals if signal.category == "ai_validation"]
    ai_risk = [
        signal
        for signal in signals
        if signal.category == "red_flag" and "ai validation" in (signal.evidence or "").lower()
    ]
    local = [signal for signal in signals if signal.category == "filing_validation"]
    red_flags = [signal for signal in signals if signal.category == "red_flag"]

    if ai_positive:
        return "AI confirmed - " + _short_signal_evidence(ai_positive[0])
    if ai_risk:
        return "AI risk flagged - " + _short_signal_evidence(ai_risk[0])
    if local:
        return "Local filing fallback - " + _short_signal_evidence(local[0])
    if red_flags:
        return "Risk fallback - " + _short_signal_evidence(red_flags[0])
    return "Not AI-validated yet; use filing/manual fallback before action"


def _compact_found_reason(score) -> str:
    signals = sorted(score.signals, key=_weighted_signal, reverse=True)
    if not signals:
        return "No clear shortlist reason."

    priority = [
        "ai_validation",
        "filing_validation",
        "capex_lifecycle",
        "turnaround",
        "financial_quality",
        "management_guidance",
        "long_term_tailwind",
        "short_term_trigger",
        "trend_momentum",
        "technical_volume",
        "red_flag",
    ]
    picked = None
    for category in priority:
        picked = next((signal for signal in signals if signal.category == category), None)
        if picked:
            break
    picked = picked or signals[0]

    reason = _plain_reason_for_signal(picked)
    evidence = _clean_evidence(picked.evidence or picked.label)
    if evidence and evidence.lower() not in reason.lower():
        reason = f"{reason}; {evidence}"
    return _clip(reason, 145)


def _plain_reason_for_signal(signal) -> str:
    label = (signal.label or "").lower()
    category = signal.category
    if category == "ai_validation":
        return "AI found material filing evidence"
    if category == "filing_validation":
        return "Filing text supports the catalyst"
    if category == "capex_lifecycle":
        return "Capex/execution cycle is connecting with demand, financials, or price confirmation"
    if category == "turnaround":
        return "Turnaround clue found: operations/profitability/capacity improving"
    if category == "financial_quality":
        return "Financial quality clue found: growth, margin, cash flow, or debt improvement"
    if category == "management_guidance":
        return "Management commentary/guidance may shift expectations"
    if category == "long_term_tailwind":
        return "Long-term sector/business tailwind detected"
    if category == "short_term_trigger":
        if "commercial production" in label or "commissioning" in label:
            return "Near-term catalyst: commercial production/commissioning"
        if "results" in label or "outcome" in label:
            return "Near-term catalyst: results/outcome filing"
        if "order" in label or "contract" in label or "loa" in label:
            return "Near-term catalyst: order/contract visibility"
        return "Near-term corporate event catalyst"
    if category == "trend_momentum":
        return "Price trend shows relative strength versus the scanned universe"
    if category == "technical_volume":
        return "Price/volume action shows market confirmation"
    if category == "red_flag":
        return "Risk item found; shortlist only for risk review"
    return "Shortlisted by highest weighted signal"


def _clean_evidence(value: str) -> str:
    value = (value or "").replace("|", "/").strip()
    prefixes = [
        "Local filing validation (pdf text parsed) found evidence in ",
        "Local filing validation (xml parsed) found evidence in ",
        "Local filing validation (metadata only) found evidence in ",
        "Codex AI validation: ",
    ]
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return _clip(value, 90)


def _short_signal_evidence(signal) -> str:
    label = (signal.label or signal.evidence or "").replace("|", "/").strip()
    return _clip(label, 72) or signal.category


def _clip(value: str, limit: int) -> str:
    value = " ".join((value or "").split())
    if len(value) > limit:
        return value[: max(0, limit - 3)].rstrip() + "..."
    return value


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
