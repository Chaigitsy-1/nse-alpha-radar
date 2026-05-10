from __future__ import annotations

import os
import re
from collections.abc import Iterable

from .config import RuntimeConfig
from .models import Company, Signal, SourceItem


_LOW_VALUE_CALENDAR_KEYWORDS = {
    "board meeting",
    "record date",
    "dividend",
}

_HIGH_VALUE_SHORT_TERM_KEYWORDS = {
    "results",
    "outcome of board meeting",
    "order win",
    "letter of award",
    "loa",
    "contract",
    "commissioning",
    "commercial production",
    "buyback",
    "bonus",
    "split",
    "preferential issue",
    "qip",
    "open offer",
    "merger",
    "acquisition",
    "credit rating upgraded",
    "rating upgrade",
}

_IGNORE_ONLY_EVENT_LABELS = {
    # These tend to be extremely noisy in filings and not edgeful alone.
    "board meeting",
    "record date",
    "dividend",
}


def _contains(text: str, keyword: str) -> bool:
    pattern = r"\b" + re.escape(keyword.lower()).replace(r"\ ", r"\s+") + r"\b"
    return re.search(pattern, text.lower()) is not None


def _match_company(item: SourceItem, companies: dict[str, Company]) -> list[Company]:
    text = item.text.lower()
    matches: list[Company] = []
    symbol_hint = item.symbol_hint.upper().strip()
    if symbol_hint:
        if symbol_hint in companies:
            return [companies[symbol_hint]]
        return []

    for company in companies.values():
        if company in matches:
            continue
        symbol_match = re.search(rf"\b{re.escape(company.symbol.lower())}\b", text)
        name_match = company.name and company.name.lower() in text
        if symbol_match or name_match:
            matches.append(company)
    return matches


def classify_source_items(
    config: RuntimeConfig,
    companies: dict[str, Company],
    items: Iterable[SourceItem],
) -> list[Signal]:
    signals: list[Signal] = []
    categories = config.signals["categories"]
    noise = config.signals.get("noise_controls", {})
    calendar_only_multiplier = float(os.getenv("CALENDAR_ONLY_MULTIPLIER", noise.get("calendar_only_multiplier", 0.35)))
    calendar_only_conf_cap = float(
        os.getenv("CALENDAR_ONLY_CONFIDENCE_CAP", noise.get("calendar_only_confidence_cap", 0.55))
    )

    for item in items:
        matched_companies = _match_company(item, companies)
        if not matched_companies:
            continue

        text = item.text
        for company in matched_companies:
            signal_text = _remove_company_identity(text, company)
            for category, definition in categories.items():
                matched_keywords = [kw for kw in definition["keywords"] if _contains(signal_text, kw)]
                if not matched_keywords:
                    continue
                base_score = float(definition["weight"]) * min(3.0, 1.0 + len(matched_keywords) * 0.25)
                confidence = min(0.9, 0.45 + len(matched_keywords) * 0.08)

                # Noise control: calendar-only triggers (board meeting / record date / dividend) are high frequency
                # and often not actionable without confirmation. Down-weight them unless paired with high-value
                # short-term keywords like results/outcome/order win/commissioning.
                if category == "short_term_trigger":
                    lowered = {kw.lower() for kw in matched_keywords}
                    has_high_value = any(kw in lowered for kw in _HIGH_VALUE_SHORT_TERM_KEYWORDS)
                    is_calendar_only = lowered.issubset(_LOW_VALUE_CALENDAR_KEYWORDS) and not has_high_value
                    if is_calendar_only:
                        base_score *= max(0.0, calendar_only_multiplier)
                        confidence = min(confidence, calendar_only_conf_cap)
                    # If a short-term trigger matches only low-value event labels, skip it entirely to reduce noise.
                    if lowered.issubset(_IGNORE_ONLY_EVENT_LABELS) and not has_high_value:
                        continue
                horizon = _horizon_for_category(category)
                signals.append(
                    Signal(
                        symbol=company.symbol,
                        company_name=company.name,
                        category=category,
                        label=", ".join(matched_keywords[:4]),
                        score=base_score,
                        confidence=confidence,
                        evidence=item.title[:280],
                        source=item.source,
                        link=item.link,
                        horizon=horizon,
                    )
                )
    return signals


def _remove_company_identity(text: str, company: Company) -> str:
    cleaned = text
    if company.name:
        cleaned = re.sub(re.escape(company.name), " ", cleaned, flags=re.IGNORECASE)
        for suffix in [" limited", " ltd.", " ltd", " india"]:
            short_name = re.sub(re.escape(suffix) + r"$", "", company.name, flags=re.IGNORECASE)
            if short_name and short_name != company.name:
                cleaned = re.sub(re.escape(short_name), " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(rf"\b{re.escape(company.symbol)}\b", " ", cleaned, flags=re.IGNORECASE)
    return cleaned


def classify_price_volume(
    config: RuntimeConfig,
    companies: dict[str, Company],
    rows: list[dict[str, str]],
) -> list[Signal]:
    signals: list[Signal] = []
    rules = config.signals["price_volume_rules"]
    for row in rows:
        symbol = (row.get("symbol") or row.get("SYMBOL") or "").upper()
        if symbol not in companies:
            continue
        company = companies[symbol]
        price_change = _to_float(row.get("price_change_pct") or row.get("pct_change"))
        volume_multiple = _to_float(row.get("volume_multiple") or row.get("volume_x"))
        delivery_multiple = _to_float(row.get("delivery_multiple") or row.get("delivery_x"))

        labels: list[str] = []
        score = 0.0
        if price_change >= rules["big_price_move_pct"]:
            labels.append(f"price +{price_change:.1f}%")
            score += 1.4
        elif price_change >= rules["strong_price_move_pct"]:
            labels.append(f"price +{price_change:.1f}%")
            score += 0.8
        if volume_multiple >= rules["volume_spike_multiple"]:
            labels.append(f"volume {volume_multiple:.1f}x")
            score += 1.1
        if delivery_multiple >= rules["delivery_spike_multiple"]:
            labels.append(f"delivery {delivery_multiple:.1f}x")
            score += 0.9
        if labels:
            source = row.get("source") or "Local price_volume.csv"
            signals.append(
                Signal(
                    symbol=symbol,
                    company_name=company.name,
                    category="technical_volume",
                    label=", ".join(labels),
                    score=score,
                    confidence=0.65,
                    evidence=f"Price-volume confirmation from {source}",
                    source=source,
                    horizon="short",
                )
            )
    return signals


def classify_fundamentals(
    config: RuntimeConfig,
    companies: dict[str, Company],
    rows: list[dict[str, str]],
) -> list[Signal]:
    signals: list[Signal] = []
    rules = config.signals["fundamental_rules"]
    for row in rows:
        symbol = (row.get("symbol") or row.get("SYMBOL") or "").upper()
        if symbol not in companies:
            continue
        company = companies[symbol]
        labels: list[str] = []
        score = 0.0
        sales_growth = _to_float(row.get("sales_growth_pct"))
        profit_growth = _to_float(row.get("profit_growth_pct"))
        margin_bps = _to_float(row.get("ebitda_margin_change_bps"))
        debt_reduction = _to_float(row.get("debt_reduction_pct"))
        previous_profit = _to_float(row.get("previous_profit"))
        latest_profit = _to_float(row.get("latest_profit"))

        if sales_growth >= rules["sales_growth_good_pct"]:
            labels.append(f"sales growth {sales_growth:.1f}%")
            score += 0.9
        if profit_growth >= rules["profit_growth_good_pct"]:
            labels.append(f"profit growth {profit_growth:.1f}%")
            score += 1.0
        if margin_bps >= rules["margin_expansion_good_bps"]:
            labels.append(f"margin +{margin_bps:.0f} bps")
            score += 1.0
        if debt_reduction >= rules["debt_reduction_good_pct"]:
            labels.append(f"debt reduction {debt_reduction:.1f}%")
            score += 0.9
        if previous_profit < 0 <= latest_profit:
            labels.append("loss to profit")
            score += 1.8

        if labels:
            category = "turnaround" if previous_profit < 0 <= latest_profit else "financial_quality"
            signals.append(
                Signal(
                    symbol=symbol,
                    company_name=company.name,
                    category=category,
                    label=", ".join(labels),
                    score=score,
                    confidence=0.72,
                    evidence="Fundamental improvement from local snapshot",
                    source="Local fundamentals.csv",
                    horizon="long" if category == "financial_quality" else "turnaround",
                )
            )
    return signals


def classify_trend_momentum(
    config: RuntimeConfig,
    companies: dict[str, Company],
    rows: list[dict[str, str]],
) -> list[Signal]:
    signals: list[Signal] = []
    rules = config.signals.get("trend_rules", {})
    ret_5_good = float(rules.get("ret_5d_good_pct", 3))
    ret_20_good = float(rules.get("ret_20d_good_pct", 10))
    ret_60_good = float(rules.get("ret_60d_good_pct", 20))
    min_ret_20_pctile = float(rules.get("min_ret_20d_percentile", 0))
    min_ret_60_pctile = float(rules.get("min_ret_60d_percentile", 0))
    overextended_20 = float(rules.get("overextended_20d_pct", 999))
    overextended_60 = float(rules.get("overextended_60d_pct", 999))
    overextended_multiplier = float(rules.get("overextended_score_multiplier", 1.0))
    overextended_conf_cap = float(rules.get("overextended_confidence_cap", 0.70))

    for row in rows:
        symbol = (row.get("symbol") or row.get("SYMBOL") or "").upper()
        if symbol not in companies:
            continue
        company = companies[symbol]

        ret_5 = _to_float(row.get("ret_5d_pct"))
        ret_20 = _to_float(row.get("ret_20d_pct"))
        ret_60 = _to_float(row.get("ret_60d_pct"))
        ret_20_pctile = _to_float(row.get("ret_20d_percentile"))
        ret_60_pctile = _to_float(row.get("ret_60d_percentile"))

        labels: list[str] = []
        score = 0.0
        cond_5 = ret_5 >= ret_5_good
        cond_20 = ret_20 >= ret_20_good
        cond_60 = ret_60 >= ret_60_good
        has_relative_strength = ret_20_pctile >= min_ret_20_pctile and ret_60_pctile >= min_ret_60_pctile

        # Require multi-horizon confirmation and cross-sectional relative strength.
        if not (((cond_20 and cond_60) or (cond_60 and cond_5)) and has_relative_strength):
            continue

        if cond_5:
            labels.append(f"5d +{ret_5:.1f}%")
            score += 0.6
        if cond_20:
            labels.append(f"20d +{ret_20:.1f}%")
            score += 1.0
        if cond_60:
            labels.append(f"60d +{ret_60:.1f}%")
            score += 1.1
        labels.append(f"RS20 p{ret_20_pctile:.0f}")
        labels.append(f"RS60 p{ret_60_pctile:.0f}")

        if labels:
            source = row.get("source") or "NSE bhavcopy trend"
            confidence = 0.70
            is_overextended = ret_20 >= overextended_20 or ret_60 >= overextended_60
            if is_overextended:
                labels.append("overextended")
                score *= max(0.0, overextended_multiplier)
                confidence = min(confidence, overextended_conf_cap)
            signals.append(
                Signal(
                    symbol=symbol,
                    company_name=company.name,
                    category="trend_momentum",
                    label=", ".join(labels),
                    score=score,
                    confidence=confidence,
                    evidence=f"Trend/momentum from {source}",
                    source=source,
                    horizon="short",
                )
            )
    return signals


def _horizon_for_category(category: str) -> str:
    if category in {"short_term_trigger", "technical_volume", "sentiment"}:
        return "short"
    if category in {"turnaround"}:
        return "turnaround"
    if category in {"long_term_tailwind", "financial_quality", "management_guidance", "capex_lifecycle"}:
        return "long"
    if category == "red_flag":
        return "risk"
    return "review"


def _to_float(value: object) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(str(value).replace(",", "").replace("%", ""))
    except ValueError:
        return 0.0
