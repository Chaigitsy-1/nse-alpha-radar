from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Iterable

from .models import CompanyScore, Signal


def write_markdown_report(
    report_date: date,
    scores: list[CompanyScore],
    output_dir: Path,
    source_errors: list[str] | None = None,
    index_stats: dict[str, dict[str, float]] | None = None,
    max_items: int = 20,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"stock_report_{report_date.isoformat()}.md"
    source_errors = source_errors or []

    actionable = _actionable_scores(scores)
    watchlist = _watchlist_scores(scores)
    sections = [
        ("Actionable Today", actionable, "total"),
        ("High-Quality Watchlist", watchlist, "total"),
        ("3-6 Month Event And Momentum", _momentum_scores(scores), "short_term"),
        ("Deep Turnaround Watch", scores, "turnaround"),
        ("1-3 Year Growth And Tailwinds", scores, "long_term"),
        ("Red Flag Review", sorted(scores, key=lambda s: s.risk, reverse=True), "risk"),
    ]

    lines: list[str] = [
        f"# Indian Midcap/Smallcap Tracker - {report_date.isoformat()}",
        "",
        "This report ranks evidence, not recommendations. Review source filings before taking action.",
        "",
        *_macro_header(report_date, index_stats or {}),
        "",
        "## How To Read Scores",
        "",
        "- Each line item is a *signal* (e.g., results/dividend filing, price move, red-flag keyword).",
        "- Signal contribution = `signal_score x confidence` (confidence is a heuristic, not a truth score).",
        "- Company totals are the sum of positive contributions minus a risk penalty: `total = (short + medium + long + turnaround) - 1.4 x risk`.",
        "",
        "## Coverage",
        "",
        f"- Companies with signals today: {len(scores)}",
        f"- Total signals captured: {sum(len(score.signals) for score in scores)}",
        f"- Actionable today: {len(actionable)}",
        f"- High-quality watchlist: {len(watchlist)}",
        "",
        *_quality_summary(scores),
        "",
    ]

    if source_errors:
        lines.extend(["## Source Warnings", ""])
        for error in source_errors[:10]:
            lines.append(f"- {error}")
        lines.append("")

    for title, section_scores, metric in sections:
        lines.extend([f"## {title}", ""])
        filtered = [score for score in section_scores if getattr(score, metric) > 0]
        if not filtered:
            if title == "Actionable Today":
                lines.extend(["No actionable high-quality opportunity today.", ""])
            else:
                lines.extend(["No high-confidence signals found in this bucket.", ""])
            continue
        for rank, score in enumerate(filtered[:max_items], start=1):
            lines.extend(_company_block(rank, score, metric))
        lines.append("")

    lines.extend(
        [
            "## Next Iteration Checklist",
            "",
            "- Add/review price-volume snapshot for breakout confirmation.",
            "- Add latest fundamentals snapshot after results season updates.",
            "- Add manual events for policy themes, management interviews, and sector rotation notes.",
            "- Mark noisy/valuable ideas so future scoring can be tuned.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _company_block(rank: int, score: CompanyScore, metric: str) -> list[str]:
    metric_value = getattr(score, metric)
    reasons = _top_signals(score.signals, metric)
    top_reasons = ", ".join(
        f"{signal.category}({signal.label})" for signal in sorted(reasons, key=_weighted_signal, reverse=True)[:2]
    )
    action, action_reason = _action_for_score(score)
    overall_conf = _overall_confidence(score.signals)
    quality_tier, quality_reason = _quality_tier_for_score(score)
    lines = [
        f"### {rank}. {score.company_name} ({score.symbol})",
        "",
        f"- Action: **{action}** ({action_reason})",
        f"- Quality Tier: **{quality_tier}** ({quality_reason})",
        f"- Confidence (overall): {overall_conf:.2f}",
        f"- Score: {metric_value:.2f} | Total: {score.total:.2f} | Risk: {score.risk:.2f}",
        f"- Risk/Reward: {_risk_reward_line(score)}",
        f"- Breakdown: short={score.short_term:.2f}, medium={score.medium_term:.2f}, long={score.long_term:.2f}, "
        f"turnaround={score.turnaround:.2f}, risk={score.risk:.2f}",
        f"- Why (top signals): {top_reasons or 'n/a'}",
        f"- Thesis: {_thesis_for_score(score)}",
    ]
    for signal in reasons:
        link = f" [{signal.source}]({signal.link})" if signal.link else f" {signal.source}"
        contribution = _weighted_signal(signal)
        lines.append(
            f"- {signal.category}: {signal.label} | horizon {signal.horizon} | "
            f"contrib {contribution:.2f} (= {signal.score:.2f}x{signal.confidence:.2f}) |{link} | {signal.evidence}"
        )

    # One-stop checklist for actionability.
    lines.extend(_next_steps(score))
    lines.append("")
    return lines


def _risk_reward_line(score: CompanyScore) -> str:
    rr = score.risk_reward
    if rr is None:
        return "unavailable (insufficient bhavcopy history)"
    return (
        f"{rr.verdict} | CMP {rr.cmp:.2f} | support {rr.support:.2f} | "
        f"stop {rr.stop_loss:.2f} | target {rr.target:.2f} | "
        f"downside {rr.downside_pct:.1f}% | upside {rr.upside_pct:.1f}% | "
        f"R/R {rr.reward_risk:.2f}x | {rr.note}"
    )


def _top_signals(signals: list[Signal], metric: str) -> list[Signal]:
    if metric == "risk":
        filtered = [signal for signal in signals if signal.horizon == "risk"]
    elif metric == "short_term":
        filtered = [signal for signal in signals if signal.horizon == "short"]
    elif metric == "turnaround":
        filtered = [signal for signal in signals if signal.horizon == "turnaround"]
    elif metric == "long_term":
        filtered = [signal for signal in signals if signal.horizon == "long"]
    else:
        filtered = signals
    return sorted(filtered, key=_weighted_signal, reverse=True)[:4]


def _weighted_signal(signal: Signal) -> float:
    return signal.score * signal.confidence


def _filtered_top_overall(scores: list[CompanyScore]) -> list[CompanyScore]:
    # Noise control: for "Top Overall", require at least one confirmation-style signal OR a meaningful total.
    # This keeps calendar-only filings from dominating the top list.
    filtered: list[CompanyScore] = []
    event_only_min_total = float(os.getenv("TOP_OVERALL_EVENT_ONLY_MIN_TOTAL", "3.0") or "3.0")

    for score in scores:
        categories = {s.category for s in score.signals}
        is_momentum_only = categories.issubset({"trend_momentum", "technical_volume"})
        if is_momentum_only:
            continue
        has_confirmation = any(
            cat in categories
            for cat in {
                "turnaround",
                "financial_quality",
                "long_term_tailwind",
                "management_guidance",
                "capex_lifecycle",
            }
        )
        has_event_only = categories.issubset({"short_term_trigger"})
        has_high_value_event = _has_high_value_short_term(score.signals)

        if has_confirmation:
            filtered.append(score)
            continue
        # Allow event-only names only when the event is "high value" (results/outcome/order/etc) and score is strong.
        if has_event_only and has_high_value_event and score.total >= event_only_min_total:
            filtered.append(score)
            continue
        if not has_event_only:
            filtered.append(score)
    return filtered


def _actionable_scores(scores: list[CompanyScore]) -> list[CompanyScore]:
    out: list[CompanyScore] = []
    for score in _filtered_top_overall(scores):
        tier, _reason = _quality_tier_for_score(score)
        categories = {signal.category for signal in score.signals}
        has_core = bool(categories & {"capex_lifecycle", "turnaround", "financial_quality"})
        has_confirmation = bool(categories & {"trend_momentum", "technical_volume", "management_guidance"})
        has_strong_lifecycle = _has_strong_lifecycle(score.signals)
        has_turnaround_confirmation = "turnaround" in categories and has_confirmation
        has_financial_confirmation = "financial_quality" in categories and has_confirmation
        if (
            tier in {"A+", "A"}
            and has_core
            and score.risk < 1.2
            and score.total >= 3.0
            and _has_acceptable_risk_reward(score)
            and (has_strong_lifecycle or has_turnaround_confirmation or has_financial_confirmation)
        ):
            out.append(score)
    return sorted(out, key=lambda score: (score.total, _overall_confidence(score.signals)), reverse=True)


def _has_acceptable_risk_reward(score: CompanyScore) -> bool:
    rr = score.risk_reward
    if rr is None:
        return False
    if rr.verdict in {"Avoid chase", "Poor R/R"}:
        return False
    return rr.reward_risk >= 1.2 and rr.downside_pct <= 15.0


def _watchlist_scores(scores: list[CompanyScore]) -> list[CompanyScore]:
    out: list[CompanyScore] = []
    for score in _filtered_top_overall(scores):
        tier, _reason = _quality_tier_for_score(score)
        if tier in {"A+", "A", "B"} and score.risk < 1.2 and score not in _actionable_scores([score]):
            out.append(score)
    return sorted(out, key=lambda score: (score.total, _overall_confidence(score.signals)), reverse=True)


def _momentum_scores(scores: list[CompanyScore]) -> list[CompanyScore]:
    # Keep C-tier momentum available for monitoring, but outside the actionable section.
    return sorted(scores, key=lambda score: score.short_term, reverse=True)


def _has_strong_lifecycle(signals: list[Signal]) -> bool:
    for signal in signals:
        if signal.category != "capex_lifecycle":
            continue
        label = (signal.label or "").lower()
        has_market = "market confirmation" in label
        has_financial = "financial follow-through" in label
        has_capacity_sequence = "capex/capacity" in label and "commissioning" in label
        has_demand = "demand/order visibility" in label
        if has_financial and (has_market or has_demand):
            return True
        if has_capacity_sequence and has_demand and has_market:
            return True
    return False


def _has_high_value_short_term(signals: Iterable[Signal]) -> bool:
    high_value_tokens = {
        "results",
        "outcome",
        "order win",
        "letter of award",
        "loa",
        "contract",
        "commissioning",
        "commercial production",
        "buyback",
        "bonus",
        "split",
        "merger",
        "acquisition",
        "rating upgrade",
        "credit rating upgraded",
        "qip",
        "preferential issue",
        "open offer",
    }
    for signal in signals:
        if signal.category != "short_term_trigger":
            continue
        label = (signal.label or "").lower()
        if any(token in label for token in high_value_tokens):
            # Exclude calendar-only combos even if label includes board meeting/dividend/record date.
            if all(tok in label for tok in ["board meeting"]) and not any(
                tok in label for tok in ["results", "outcome", "order win", "commissioning", "commercial production"]
            ):
                continue
            return True
    return False


def _overall_confidence(signals: Iterable[Signal]) -> float:
    weighted = [(_weighted_signal(signal), signal.confidence) for signal in signals]
    total = sum(w for w, _ in weighted if w > 0)
    if total <= 0:
        return 0.0
    return sum(w * c for w, c in weighted if w > 0) / total


def _action_for_score(score: CompanyScore) -> tuple[str, str]:
    categories = {signal.category for signal in score.signals}
    has_tech = "technical_volume" in categories
    has_quality = bool(categories & {"financial_quality", "long_term_tailwind", "management_guidance", "capex_lifecycle"})
    has_turnaround = "turnaround" in categories
    has_red_flag = "red_flag" in categories or score.risk >= 1.2

    if has_red_flag and score.total < 3.0:
        return "Avoid / Review Risk", "risk signals outweigh upside"
    if has_turnaround and has_red_flag:
        return "Speculative Watch", "turnaround with risk; needs confirmation"
    if has_tech and has_quality and not has_red_flag:
        return "High-Priority Watchlist", "confirmation + thesis signals align"
    if has_tech and not has_red_flag:
        return "Watch For Entry", "momentum/price confirmation present"
    if has_quality and not has_red_flag:
        return "Research Candidate", "medium/long-term thesis signals present"
    return "Low-Priority Watch", "event/keyword signal; needs confirmation"


def _quality_tier_for_score(score: CompanyScore) -> tuple[str, str]:
    categories = {signal.category for signal in score.signals}
    confidence = _overall_confidence(score.signals)
    has_red_flag = "red_flag" in categories or score.risk >= 1.2
    has_lifecycle = "capex_lifecycle" in categories
    has_turnaround = "turnaround" in categories
    has_financial = "financial_quality" in categories
    has_guidance = "management_guidance" in categories
    has_tailwind = "long_term_tailwind" in categories
    has_market_confirmation = bool(categories & {"technical_volume", "trend_momentum"})
    has_noisy_event_only = categories and categories.issubset({"short_term_trigger"})
    has_quality_context = bool(categories & {"capex_lifecycle", "turnaround", "financial_quality", "management_guidance", "long_term_tailwind"})

    if has_red_flag and score.total < 3.0:
        return "Avoid", "red flag/risk dominates"
    if has_lifecycle and (has_financial or has_guidance or has_market_confirmation) and confidence >= 0.60:
        return "A+", "lifecycle plus confirmation"
    if (has_lifecycle or has_turnaround) and (has_market_confirmation or has_guidance or has_financial):
        return "A", "thesis plus confirmation"
    if has_quality_context and (score.total >= 2.0 or confidence >= 0.60):
        return "B", "thesis signal, needs stronger proof"
    if has_market_confirmation and not has_quality_context:
        return "C", "momentum without business thesis"
    if has_noisy_event_only:
        return "D", "event/calendar heavy"
    return "C", "mixed or incomplete evidence"


def _quality_summary(scores: list[CompanyScore]) -> list[str]:
    counts = {"A+": 0, "A": 0, "B": 0, "C": 0, "D": 0, "Avoid": 0}
    for score in scores:
        tier, _reason = _quality_tier_for_score(score)
        counts[tier] = counts.get(tier, 0) + 1
    return [
        "## Quality Funnel",
        "",
        f"- A+ opportunities: {counts['A+']}",
        f"- A opportunities: {counts['A']}",
        f"- B opportunities: {counts['B']}",
        f"- C opportunities: {counts['C']}",
        f"- D/noisy opportunities: {counts['D']}",
        f"- Avoid/risk opportunities: {counts['Avoid']}",
    ]


def _thesis_for_score(score: CompanyScore) -> str:
    # Compact, human-readable reason summary based on top weighted signals across horizons.
    top = sorted(score.signals, key=_weighted_signal, reverse=True)[:3]
    if not top:
        return "No strong thesis signals."
    parts: list[str] = []
    for signal in top:
        if signal.category == "technical_volume":
            parts.append("Price/volume confirmation suggests near-term momentum.")
        elif signal.category == "trend_momentum":
            label = (signal.label or "").lower()
            if "overextended" in label:
                parts.append("Relative strength is present, but the move may be stretched; wait for cleaner risk/reward.")
            else:
                parts.append("Multi-horizon relative strength suggests institutional attention or sustained sentiment.")
        elif signal.category == "financial_quality":
            parts.append("Fundamental quality improving (growth/margins/cashflow/deleveraging).")
        elif signal.category == "turnaround":
            parts.append("Turnaround setup (improving operations/profitability) - higher risk/reward.")
        elif signal.category == "long_term_tailwind":
            parts.append("Sector/theme tailwind could support multi-quarter growth expectations.")
        elif signal.category == "capex_lifecycle":
            parts.append("Capex lifecycle evidence is connecting capacity, execution, demand, financial follow-through, or market confirmation.")
        elif signal.category == "management_guidance":
            parts.append("Management guidance/presentation may shift expectations and drive re-rating.")
        elif signal.category == "short_term_trigger":
            label = (signal.label or "").lower()
            if "results" in label or "outcome" in label:
                parts.append("Earnings/results catalyst can trigger re-rating.")
            elif "order win" in label or "contract" in label or "loa" in label:
                parts.append("Order/catalyst may improve visibility and sentiment.")
            else:
                parts.append("Corporate event catalyst may drive short-term attention.")
        elif signal.category == "red_flag":
            parts.append("Risk disclosure present; upside may be capped or delayed.")
    # De-duplicate similar sentences while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        deduped.append(part)
    return " ".join(deduped)[:280]


def _next_steps(score: CompanyScore) -> list[str]:
    categories = {signal.category for signal in score.signals}
    steps: list[str] = ["- Next steps:"]
    if "short_term_trigger" in categories:
        steps.append("  - Open the linked NSE filing(s); confirm it is an *outcome/results/order* vs mere intimation.")
    if "technical_volume" in categories:
        steps.append("  - Check chart context (trend + recent highs); avoid chasing if move is purely index-driven.")
    if "trend_momentum" in categories:
        steps.append("  - Compare 20d/60d relative strength against Midcap/Smallcap; avoid entries after vertical moves.")
    if "financial_quality" in categories or "turnaround" in categories:
        steps.append("  - Validate fundamentals (profitability trend, debt, cashflow) from results/annual report.")
    if "long_term_tailwind" in categories:
        steps.append("  - Map the tailwind to company execution (capacity, order book, customers, competitive moat).")
    if "capex_lifecycle" in categories:
        steps.append("  - Trace the sequence: capex -> commissioning -> utilization -> revenue/profit -> guidance -> price confirmation.")
    if "red_flag" in categories or score.risk >= 1.2:
        steps.append("  - Read the red-flag filing; decide if it's temporary noise or structural risk.")
    if len(steps) == 1:
        steps.append("  - Treat as a low-signal mention; wait for confirmation.")
    return steps


def _macro_header(report_date: date, index_stats: dict[str, dict[str, float]]) -> list[str]:
    mid = index_stats.get("Nifty Midcap 150", {})
    small = index_stats.get("Nifty Smallcap 250", {})
    if not mid and not small:
        return ["## Market Regime", "", "- Market regime: unavailable (index close file missing)"]

    regime = _regime_from_index_move(mid.get("pct_change"), small.get("pct_change"))
    lines = ["## Market Regime", "", f"- Date: {report_date.isoformat()} | Regime: **{regime}**"]
    if "close" in mid:
        lines.append(f"- Nifty Midcap 150: close {mid['close']:.2f} | day % {mid.get('pct_change', 0.0):.2f}%")
    if "close" in small:
        lines.append(f"- Nifty Smallcap 250: close {small['close']:.2f} | day % {small.get('pct_change', 0.0):.2f}%")
    return lines


def _regime_from_index_move(midcap_pct: float | None, smallcap_pct: float | None) -> str:
    rets = [r for r in [midcap_pct, smallcap_pct] if r is not None]
    if not rets:
        return "unknown"
    avg = sum(rets) / len(rets)
    if avg <= -1.0:
        return "risk_off"
    if avg >= 1.0:
        return "risk_on"
    return "neutral"
