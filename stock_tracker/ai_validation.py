from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .filing_validation import _compact_text, _extract_filing_text, _is_important_item
from .models import Company, Signal, SourceItem


@dataclass(frozen=True)
class AIValidationSignal:
    symbol: str
    company_name: str
    category: str
    label: str
    score: float
    confidence: float
    evidence: str
    source: str
    link: str
    horizon: str


def build_ai_validation_signals(
    companies: dict[str, Company],
    items: list[SourceItem],
    *,
    cache_dir: Path,
    queue_dir: Path,
    results_dir: Path,
    report_date: date,
    max_filings: int | None = None,
    priority_symbols: set[str] | None = None,
    priority_contexts: list[dict[str, Any]] | None = None,
) -> tuple[list[Signal], list[str]]:
    provider = os.getenv("AI_PROVIDER", "none").strip().lower()
    if provider in {"", "none", "off", "false", "0"}:
        return [], []

    max_filings = max_filings or int(os.getenv("AI_VALIDATION_MAX_FILINGS", "8") or "8")
    priority_symbols = {symbol.upper().strip() for symbol in (priority_symbols or set()) if symbol}
    candidates, errors = _candidate_packets(
        companies,
        items,
        cache_dir=cache_dir,
        max_filings=max_filings,
        priority_symbols=priority_symbols,
    )
    context_packets = _context_packets(priority_contexts or [], report_date=report_date)
    if context_packets:
        candidates = context_packets + candidates
        errors.append(f"AI high-conviction thesis review queued/checked for {len(context_packets)} stock(s).")
    if not candidates:
        return [], errors

    signals: list[Signal] = []
    if provider == "codex_queue":
        queued, reviewed, merged = _codex_queue_validation(
            candidates,
            queue_dir=queue_dir / report_date.isoformat(),
            results_dir=results_dir / report_date.isoformat(),
        )
        signals.extend(_to_signal(result, provider="Codex queue") for result in merged)
        if queued:
            errors.append(
                f"AI validation queued {queued} filing(s) for Codex; rerun after result JSON is written."
            )
        if reviewed:
            errors.append(f"AI validation reviewed {reviewed} Codex result(s); {len(merged)} became scoring signal(s).")
    elif provider == "ollama":
        provider_signals, provider_errors = _run_model_provider(candidates, provider="ollama")
        signals.extend(provider_signals)
        errors.extend(provider_errors)
    elif provider == "openrouter":
        provider_signals, provider_errors = _run_model_provider(candidates, provider="openrouter")
        signals.extend(provider_signals)
        errors.extend(provider_errors)
    else:
        errors.append(f"AI validation skipped: unknown AI_PROVIDER={provider!r}.")

    return signals, errors


def _candidate_packets(
    companies: dict[str, Company],
    items: list[SourceItem],
    *,
    cache_dir: Path,
    max_filings: int,
    priority_symbols: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    packets: list[dict[str, Any]] = []
    seen: set[str] = set()
    priority_max_filings = int(os.getenv("AI_VALIDATION_HIGH_CONVICTION_MAX_FILINGS", "80") or "80")
    priority_count = 0
    regular_count = 0
    important_priority_symbols: set[str] = set()

    ordered_items = sorted(
        items,
        key=lambda item: 0 if item.symbol_hint.upper().strip() in priority_symbols else 1,
    )

    for item in ordered_items:
        symbol = item.symbol_hint.upper().strip()
        if not symbol or symbol not in companies or not _is_important_item(item):
            continue
        is_priority = symbol in priority_symbols
        if is_priority:
            if priority_count >= priority_max_filings:
                continue
            priority_count += 1
            important_priority_symbols.add(symbol)
        else:
            if regular_count >= max_filings:
                continue
            regular_count += 1
        packet_id = _packet_id(item)
        if packet_id in seen:
            continue
        seen.add(packet_id)

        extracted_text, extraction_note, extraction_errors = _extract_filing_text(item, cache_dir=cache_dir)
        errors.extend(extraction_errors)
        combined = _compact_text(" ".join([item.title, item.summary, extracted_text]))
        if len(combined) < 80:
            continue

        company = companies[symbol]
        packets.append(
            {
                "id": packet_id,
                "symbol": symbol,
                "company_name": company.name,
                "filing_title": item.title,
                "filing_summary": item.summary,
                "link": item.link,
                "source": item.source,
                "published": item.published,
                "extraction_note": extraction_note,
                "text": combined[:6500],
                "instructions": _validation_instructions(),
            }
        )
    if regular_count >= max_filings:
        errors.append(f"AI validation candidate scan capped at {max_filings} important filing(s).")
    if priority_count >= priority_max_filings:
        errors.append(f"AI high-conviction filing scan capped at {priority_max_filings} filing(s).")
    missing_priority = sorted(priority_symbols - important_priority_symbols)
    if missing_priority:
        shown = ", ".join(missing_priority[:12])
        suffix = f", +{len(missing_priority) - 12} more" if len(missing_priority) > 12 else ""
        errors.append(f"AI filing scan found no important fresh filing for high-conviction symbol(s): {shown}{suffix}.")
    return packets, errors


def _context_packets(contexts: list[dict[str, Any]], *, report_date: date) -> list[dict[str, Any]]:
    max_contexts = int(os.getenv("AI_VALIDATION_HIGH_CONVICTION_MAX_SYMBOLS", "40") or "40")
    packets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for context in contexts[:max_contexts]:
        symbol = str(context.get("symbol", "")).upper().strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        text = _compact_text(str(context.get("text", "")))
        packet_id = hashlib.sha1(f"{report_date.isoformat()}|high-conviction|{symbol}|{text[:500]}".encode("utf-8")).hexdigest()[:16]
        packets.append(
            {
                "id": packet_id,
                "symbol": symbol,
                "company_name": str(context.get("company_name", symbol)),
                "filing_title": f"High-conviction thesis review - {symbol}",
                "filing_summary": str(context.get("summary", "")),
                "link": "",
                "source": "NSE Alpha Radar high-conviction thesis",
                "published": report_date.isoformat(),
                "extraction_note": "report signals context",
                "text": text[:6500],
                "instructions": _validation_instructions(),
            }
        )
    return packets


def _codex_queue_validation(
    packets: list[dict[str, Any]],
    *,
    queue_dir: Path,
    results_dir: Path,
) -> tuple[int, int, list[AIValidationSignal]]:
    queue_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    queued = 0
    reviewed = 0
    merged: list[AIValidationSignal] = []

    for packet in packets:
        result_path = results_dir / f"{packet['id']}.json"
        if result_path.exists():
            parsed = _load_result(result_path)
            reviewed += 1
            result = _result_to_validation(packet, parsed, source="Codex AI validation")
            if result:
                merged.append(result)
            continue
        queue_path = queue_dir / f"{packet['id']}.json"
        if not queue_path.exists():
            queue_path.write_text(json.dumps(packet, indent=2), encoding="utf-8")
        queued += 1

    return queued, reviewed, merged


def _run_model_provider(packets: list[dict[str, Any]], *, provider: str) -> tuple[list[Signal], list[str]]:
    out: list[Signal] = []
    errors: list[str] = []
    for packet in packets:
        try:
            parsed = _call_ai_provider(packet, provider=provider)
        except Exception as exc:
            errors.append(f"AI validation skipped {packet['symbol']} via {provider}: {exc}")
            continue
        if not parsed:
            errors.append(f"AI validation produced no usable result for {packet['symbol']} via {provider}.")
            continue
        result = _result_to_validation(packet, parsed, source=f"{provider.title()} AI validation")
        if result:
            out.append(_to_signal(result, provider=f"{provider.title()} AI validation"))
    return out, errors


def _call_ai_provider(packet: dict[str, Any], *, provider: str) -> dict[str, Any]:
    prompt = _prompt_for_packet(packet)
    if provider == "ollama":
        model = os.getenv("OLLAMA_MODEL", "").strip()
        if not model:
            return {}
        payload = {"model": model, "prompt": prompt, "stream": False, "format": "json"}
        url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate").strip()
        data = _post_json(url, payload, headers={})
        return _parse_jsonish(str(data.get("response", "")))

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return {}
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini").strip()
    url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions").strip()
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "You validate Indian stock exchange filings. Return only strict JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    data = _post_json(
        url,
        payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/Chaigitsy-1/nse-alpha-radar",
            "X-Title": "nse-alpha-radar",
        },
    )
    choices = data.get("choices") or []
    if not choices:
        return {}
    message = choices[0].get("message") or {}
    return _parse_jsonish(str(message.get("content", "")))


def _post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=75) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _result_to_validation(
    packet: dict[str, Any],
    parsed: dict[str, Any],
    *,
    source: str,
) -> AIValidationSignal | None:
    verdict = str(parsed.get("verdict", "")).strip().upper()
    materiality = str(parsed.get("materiality", "")).strip().lower()
    sentiment = str(parsed.get("sentiment", "")).strip().lower()
    if verdict not in {"PASS", "PARTIAL", "FAIL"}:
        return None

    confidence = _clamp_float(parsed.get("confidence"), default=0.58, low=0.35, high=0.86)
    reason = str(parsed.get("reason", "")).strip()
    evidence = parsed.get("key_evidence", [])
    if isinstance(evidence, list):
        evidence_text = "; ".join(str(item).strip() for item in evidence if str(item).strip())
    else:
        evidence_text = str(evidence).strip()
    if not evidence_text:
        evidence_text = reason or packet["filing_title"]

    if verdict == "FAIL" and sentiment != "negative" and materiality != "high":
        return None

    if verdict == "FAIL" or sentiment == "negative":
        category = "red_flag"
        horizon = "risk"
        score = 1.4 if materiality == "high" else 1.0
        label = f"AI flagged filing: {materiality or 'unknown'} materiality"
    else:
        category = _normal_category(str(parsed.get("category", "")).strip().lower())
        horizon = "turnaround" if category == "turnaround" else "long"
        score = 1.7 if materiality == "high" else 1.25
        if verdict == "PARTIAL":
            score *= 0.8
            confidence = min(confidence, 0.68)
        label = f"AI validated filing: {verdict.lower()}, {materiality or 'unknown'} materiality"

    return AIValidationSignal(
        symbol=str(packet["symbol"]),
        company_name=str(packet["company_name"]),
        category=category,
        label=label,
        score=score,
        confidence=confidence,
        evidence=f"{source}: {evidence_text[:240]}",
        source=source,
        link=str(packet.get("link", "")),
        horizon=horizon,
    )


def _to_signal(validation: AIValidationSignal, *, provider: str) -> Signal:
    return Signal(
        symbol=validation.symbol,
        company_name=validation.company_name,
        category=validation.category,
        label=validation.label,
        score=validation.score,
        confidence=validation.confidence,
        evidence=validation.evidence,
        source=provider,
        link=validation.link,
        horizon=validation.horizon,
    )


def _load_result(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _normal_category(value: str) -> str:
    allowed = {
        "ai_validation",
        "filing_validation",
        "turnaround",
        "financial_quality",
        "management_guidance",
        "long_term_tailwind",
    }
    if value in allowed:
        return "ai_validation" if value == "filing_validation" else value
    return "ai_validation"


def _clamp_float(value: Any, *, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(high, max(low, parsed))


def _packet_id(item: SourceItem) -> str:
    raw = "|".join([item.symbol_hint, item.title, item.link, item.published])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _prompt_for_packet(packet: dict[str, Any]) -> str:
    return (
        _validation_instructions()
        + "\n\n"
        + f"Company: {packet['company_name']} ({packet['symbol']})\n"
        + f"Filing: {packet['filing_title']}\n"
        + f"Source: {packet.get('source', '')} | Published: {packet.get('published', '')}\n"
        + f"Link: {packet.get('link', '')}\n"
        + f"Extraction: {packet.get('extraction_note', '')}\n\n"
        + f"Text:\n{packet['text'][:6500]}"
    )


def _validation_instructions() -> str:
    return (
        "Validate whether this filing or high-conviction report thesis creates an investable signal for an Indian mid/small-cap stock. "
        "Focus on durable evidence: capex commissioning, utilization, revenue/profit jump, order book, "
        "margin expansion, debt reduction, management guidance, turnaround, and sector tailwind linkage. "
        "Reject boilerplate, calendar-only notices, weak sentiment, one-off accounting gains, and unverified hype. "
        "Do not invent numbers. Return strict JSON only with keys: verdict (PASS/PARTIAL/FAIL), "
        "materiality (high/medium/low), sentiment (positive/neutral/negative), confidence (0 to 1), "
        "category (ai_validation/turnaround/financial_quality/management_guidance/long_term_tailwind/red_flag), "
        "reason (short), key_evidence (array of short evidence strings), manual_checks (array of checks)."
    )


def _parse_jsonish(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}
