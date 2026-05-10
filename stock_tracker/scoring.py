from __future__ import annotations

from .models import Company, CompanyScore, Signal


def _signal_dedup_key(signal: Signal) -> tuple[str, str, str, str]:
    label = " ".join((signal.label or "").lower().split())
    evidence = " ".join((signal.evidence or "").lower().split())
    # Keep key stable but not too granular; filings often repeat with tiny variations.
    return (signal.symbol, signal.category, label[:120], evidence[:120])


def _dedup_signals(signals: list[Signal]) -> list[Signal]:
    best_by_key: dict[tuple[str, str, str, str], Signal] = {}
    for signal in signals:
        key = _signal_dedup_key(signal)
        existing = best_by_key.get(key)
        if not existing:
            best_by_key[key] = signal
            continue
        existing_weighted = existing.score * existing.confidence
        candidate_weighted = signal.score * signal.confidence
        if candidate_weighted > existing_weighted:
            best_by_key[key] = signal
    return list(best_by_key.values())


def _filter_noisy_signals(signals: list[Signal]) -> list[Signal]:
    """
    Backtest-driven noise reduction:
    - 1-day `technical_volume` spikes tend to mean-revert unless aligned with multi-horizon trend strength.
      Keep `technical_volume` only when `trend_momentum` is also present for the same symbol.
    """
    by_symbol: dict[str, list[Signal]] = {}
    for signal in signals:
        by_symbol.setdefault(signal.symbol, []).append(signal)

    filtered: list[Signal] = []
    for symbol, symbol_signals in by_symbol.items():
        has_trend = any(s.category == "trend_momentum" for s in symbol_signals)
        for signal in symbol_signals:
            if signal.category == "technical_volume" and not has_trend:
                continue
            filtered.append(signal)
    return filtered


def score_companies(companies: dict[str, Company], signals: list[Signal]) -> list[CompanyScore]:
    scores = {
        symbol: CompanyScore(symbol=symbol, company_name=company.name)
        for symbol, company in companies.items()
    }

    filtered_signals = _filter_noisy_signals(_dedup_signals(signals))
    for signal in filtered_signals:
        company_score = scores.get(signal.symbol)
        if not company_score:
            continue
        weighted = signal.score * signal.confidence
        company_score.signals.append(signal)
        if signal.horizon == "short":
            company_score.short_term += weighted
        elif signal.horizon == "turnaround":
            company_score.turnaround += weighted
            company_score.medium_term += weighted * 0.6
        elif signal.horizon == "long":
            company_score.long_term += weighted
            company_score.medium_term += weighted * 0.5
        elif signal.horizon == "risk":
            company_score.risk += abs(weighted)
        else:
            company_score.medium_term += weighted * 0.4

    for company_score in scores.values():
        positive = (
            company_score.short_term
            + company_score.medium_term
            + company_score.long_term
            + company_score.turnaround
        )
        company_score.total = positive - company_score.risk * 1.4

        # Penalize event-only names (short_term_trigger only). This reduces calendar noise dominating totals.
        categories = {signal.category for signal in company_score.signals}
        if categories and categories.issubset({"short_term_trigger"}):
            company_score.total *= 0.65

    return sorted(
        [score for score in scores.values() if score.signals],
        key=lambda item: item.total,
        reverse=True,
    )
