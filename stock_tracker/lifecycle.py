from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from .models import Signal


_CAPEX_TERMS = {
    "capex",
    "capacity expansion",
    "new capacity",
    "expanded capacity",
    "capacity addition",
    "capacity enhancement",
    "greenfield",
    "brownfield",
    "new plant",
    "new facility",
    "debottlenecking",
    "backward integration",
}

_COMMISSIONING_TERMS = {
    "commissioning",
    "commercial production",
    "commercialized capacity",
    "plant restart",
    "launch",
    "phase 1",
    "phase 2",
}

_UTILIZATION_TERMS = {
    "capacity utilization",
    "capacity utilisation",
    "utilization improved",
    "utilisation improved",
    "higher utilization",
    "higher utilisation",
    "ramp up",
    "ramp-up",
    "utilization ramp up",
    "utilisation ramp up",
    "operating leverage",
}

_DEMAND_TERMS = {
    "order book",
    "order win",
    "letter of award",
    "loa",
    "contract",
    "purchase order",
    "revenue visibility",
    "pipeline",
    "demand outlook",
    "strong demand",
    "robust demand",
}

_FINANCIAL_TERMS = {
    "sales growth",
    "revenue growth",
    "profit growth",
    "pat growth",
    "volume growth",
    "margin expansion",
    "margin +",
    "ebitda margin",
    "loss to profit",
    "profitability improved",
    "profitability improvement",
    "debt reduction",
}


_REPORT_RE = re.compile(r"stock_report_(\d{4}-\d{2}-\d{2})\.md$")
_HEADING_RE = re.compile(r"^###\s+\d+\.\s+(.+?)\s+\(([A-Z0-9&-]+)\)\s*$")
_SIGNAL_RE = re.compile(r"^-\s+([a-z_]+):\s+(.+?)\s+\|")


def build_capex_lifecycle_signals(
    signals: list[Signal],
    *,
    report_date: date | None = None,
    history_dir: Path | None = None,
    lookback_days: int = 365,
) -> list[Signal]:
    by_symbol: dict[str, list[Signal]] = defaultdict(list)
    for signal in signals:
        by_symbol[signal.symbol].append(signal)

    if report_date and history_dir:
        for historical_signal in _load_historical_report_signals(
            history_dir=history_dir,
            report_date=report_date,
            lookback_days=lookback_days,
        ):
            by_symbol[historical_signal.symbol].append(historical_signal)

    lifecycle_signals: list[Signal] = []
    for symbol, symbol_signals in by_symbol.items():
        current_signals = [signal for signal in symbol_signals if signal in signals]
        current_stages = _detect_stages(current_signals)
        stages = _detect_stages(symbol_signals)
        core_stages = {"capex_announced", "commissioning", "utilization_ramp"}
        if len(stages) < 2 or not (set(stages) & core_stages):
            continue
        if not current_stages:
            continue

        has_follow_through = bool(
            set(stages)
            & {
                "demand_visibility",
                "financial_follow_through",
                "management_guidance",
                "market_confirmation",
            }
        )
        if not has_follow_through:
            continue
        if not _has_current_lifecycle_relevance(current_stages):
            continue

        company_name = current_signals[0].company_name if current_signals else symbol_signals[0].company_name
        stage_names = _ordered_stage_names(stages)
        base_score = 1.0 + min(3.2, len(stages) * 0.45)
        if "financial_follow_through" in stages:
            base_score += 0.45
        if "commissioning" in stages and "utilization_ramp" in stages:
            base_score += 0.35
        confidence = min(0.84, 0.48 + len(stages) * 0.07)
        evidence = _evidence_summary(symbol_signals, stages)

        lifecycle_signals.append(
            Signal(
                symbol=symbol,
                company_name=company_name,
                category="capex_lifecycle",
                label=" -> ".join(stage_names),
                score=base_score,
                confidence=confidence,
                evidence=evidence,
                source="Derived capex lifecycle",
                horizon="long",
            )
        )
    return lifecycle_signals


def _has_current_lifecycle_relevance(stages: set[str]) -> bool:
    return bool(
        stages
        & {
            "capex_announced",
            "commissioning",
            "utilization_ramp",
            "demand_visibility",
            "financial_follow_through",
            "management_guidance",
            "market_confirmation",
        }
    )


def _detect_stages(signals: list[Signal]) -> set[str]:
    stages: set[str] = set()
    for signal in signals:
        text = " ".join([signal.category, signal.label, signal.evidence]).lower()
        if signal.category == "long_term_tailwind" and _has_any(text, _CAPEX_TERMS):
            stages.add("capex_announced")
        if _has_any(text, _COMMISSIONING_TERMS):
            stages.add("commissioning")
        if _has_any(text, _UTILIZATION_TERMS):
            stages.add("utilization_ramp")
        if signal.category in {"short_term_trigger", "management_guidance"} and _has_any(text, _DEMAND_TERMS):
            stages.add("demand_visibility")
        if signal.category == "management_guidance":
            stages.add("management_guidance")
        if signal.category in {"financial_quality", "turnaround"} and _has_any(text, _FINANCIAL_TERMS):
            stages.add("financial_follow_through")
        if signal.category in {"technical_volume", "trend_momentum"}:
            stages.add("market_confirmation")
    return stages


def _has_any(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def _ordered_stage_names(stages: set[str]) -> list[str]:
    order = [
        ("capex_announced", "capex/capacity"),
        ("commissioning", "commissioning"),
        ("utilization_ramp", "utilization/ramp-up"),
        ("demand_visibility", "demand/order visibility"),
        ("financial_follow_through", "financial follow-through"),
        ("management_guidance", "management guidance"),
        ("market_confirmation", "market confirmation"),
    ]
    return [label for key, label in order if key in stages]


def _evidence_summary(signals: list[Signal], stages: set[str]) -> str:
    examples: list[str] = []
    for signal in sorted(signals, key=lambda item: item.score * item.confidence, reverse=True):
        if signal.category in {
            "long_term_tailwind",
            "management_guidance",
            "financial_quality",
            "turnaround",
            "short_term_trigger",
            "technical_volume",
            "trend_momentum",
        }:
            examples.append(f"{signal.category}: {signal.label}")
        if len(examples) >= 3:
            break
    stage_text = ", ".join(_ordered_stage_names(stages))
    return f"Lifecycle stages detected: {stage_text}. Evidence: {' | '.join(examples)}"


def _load_historical_report_signals(
    *,
    history_dir: Path,
    report_date: date,
    lookback_days: int,
) -> list[Signal]:
    if not history_dir.exists():
        return []
    out: list[Signal] = []
    for path in sorted(history_dir.glob("stock_report_*.md")):
        match = _REPORT_RE.search(path.name)
        if not match:
            continue
        try:
            historical_date = datetime.strptime(match.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        age = (report_date - historical_date).days
        if age <= 0 or age > lookback_days:
            continue
        out.extend(_parse_report_signals(path, historical_date))
    return out


def _parse_report_signals(path: Path, historical_date: date) -> list[Signal]:
    current_symbol = ""
    current_company = ""
    parsed: list[Signal] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            current_company = heading.group(1)
            current_symbol = heading.group(2)
            continue
        if not current_symbol:
            continue
        signal_match = _SIGNAL_RE.match(line)
        if not signal_match:
            continue
        category = signal_match.group(1)
        label = signal_match.group(2)
        if category == "capex_lifecycle":
            continue
        parsed.append(
            Signal(
                symbol=current_symbol,
                company_name=current_company,
                category=category,
                label=label,
                score=0.1,
                confidence=0.4,
                evidence=f"Historical report {historical_date.isoformat()}: {line[:220]}",
                source=f"Historical report {historical_date.isoformat()}",
                horizon="review",
            )
        )
    return parsed
