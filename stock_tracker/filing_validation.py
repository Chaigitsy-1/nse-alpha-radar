from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .models import Company, Signal, SourceItem


USER_AGENT = "Mozilla/5.0 IndianStockTracker/0.1"

_IMPORTANT_EVENT_TERMS = {
    "results",
    "outcome",
    "investor presentation",
    "press release",
    "concall",
    "earnings call",
    "analyst meet",
    "business update",
    "order",
    "contract",
    "letter of award",
    "loa",
    "commercial production",
    "commissioning",
    "capacity",
    "capex",
}

_POSITIVE_TERMS = {
    "revenue growth",
    "sales growth",
    "profit growth",
    "pat growth",
    "ebitda",
    "margin expansion",
    "margin improved",
    "order book",
    "strong demand",
    "growth guidance",
    "revenue visibility",
    "capacity utilization",
    "capacity utilisation",
    "operating leverage",
    "debt reduction",
    "free cash flow",
}

_TURNAROUND_TERMS = {
    "loss to profit",
    "profitability improved",
    "ebitda positive",
    "turnaround",
    "loss reduced",
    "plant restart",
}

_CAPEX_TERMS = {
    "capex",
    "capacity expansion",
    "commissioning",
    "commercial production",
    "new plant",
    "new facility",
    "ramp up",
    "debottlenecking",
}

_RED_FLAG_TERMS = {
    "qualified opinion",
    "auditor resignation",
    "default",
    "fraud",
    "forensic audit",
    "rating downgraded",
    "pledge",
    "litigation",
    "penalty",
}


@dataclass(frozen=True)
class FilingValidation:
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


def build_filing_validation_signals(
    companies: dict[str, Company],
    items: list[SourceItem],
    *,
    cache_dir: Path,
    max_filings: int | None = None,
) -> tuple[list[Signal], list[str]]:
    """
    Validates important filings with deterministic local parsing first.

    PDF parsing is intentionally conservative: if no local PDF parser is installed,
    the engine still uses NSE-provided text/title metadata and cached attachment bytes.
    Optional Ollama classification is available only when OLLAMA_MODEL is set.
    """
    max_filings = max_filings or int(os.getenv("FILING_VALIDATION_MAX_FILINGS", "45") or "45")
    cache_dir.mkdir(parents=True, exist_ok=True)

    signals: list[Signal] = []
    errors: list[str] = []
    checked = 0

    for item in items:
        if checked >= max_filings:
            break
        symbol = item.symbol_hint.upper().strip()
        if not symbol or symbol not in companies:
            continue
        if not _is_important_item(item):
            continue

        checked += 1
        company = companies[symbol]
        extracted_text, extraction_note, extraction_errors = _extract_filing_text(item, cache_dir=cache_dir)
        errors.extend(extraction_errors)
        combined = _compact_text(" ".join([item.title, item.summary, extracted_text]))
        deterministic = _deterministic_validation(symbol, company.name, item, combined, extraction_note)
        if deterministic:
            signals.append(_to_signal(deterministic))

        model_signal = _ollama_validation(symbol, company.name, item, combined)
        if model_signal:
            signals.append(_to_signal(model_signal))

    if checked >= max_filings:
        errors.append(f"Filing validation capped at {max_filings} important filings for this run.")
    return signals, errors


def _is_important_item(item: SourceItem) -> bool:
    text = item.text.lower()
    return any(term in text for term in _IMPORTANT_EVENT_TERMS)


def _extract_filing_text(item: SourceItem, *, cache_dir: Path) -> tuple[str, str, list[str]]:
    if not item.link:
        return "", "metadata only", []
    link = item.link.strip()
    suffix = Path(link.split("?", 1)[0]).suffix.lower()
    errors: list[str] = []
    text = ""
    note = "metadata only"

    try:
        path = _download_attachment(link, cache_dir=cache_dir)
    except Exception as exc:
        return "", "download failed", [f"Filing validation download failed for {link}: {exc}"]

    if suffix in {".xml", ".xbrl"}:
        try:
            text = _extract_xml_text(path)
            note = "xml parsed"
        except Exception as exc:
            errors.append(f"Filing validation XML parse failed for {link}: {exc}")
    elif suffix == ".pdf":
        text = _extract_pdf_text_best_effort(path)
        note = "pdf text parsed" if text else "pdf cached; parser unavailable"
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            note = "text parsed"
        except Exception:
            note = "attachment cached"
    return text[:12000], note, errors


def _download_attachment(url: str, *, cache_dir: Path) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    suffix = Path(url.split("?", 1)[0]).suffix.lower() or ".bin"
    path = cache_dir / f"{digest}{suffix}"
    if path.exists():
        return path
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=30) as response:
        path.write_bytes(response.read())
    return path


def _extract_xml_text(path: Path) -> str:
    root = ET.fromstring(path.read_text(encoding="utf-8", errors="replace"))
    values: list[str] = []
    for node in root.iter():
        if node.text and node.text.strip():
            values.append(node.text.strip())
    return _compact_text(" ".join(values))


def _extract_pdf_text_best_effort(path: Path) -> str:
    # Dependency-free fallback. Many PDFs compress streams, so this is imperfect,
    # but it can recover text from simpler filings without adding mandatory packages.
    raw = path.read_bytes()
    text = raw.decode("latin-1", errors="ignore")
    chunks = re.findall(r"\(([^()]{3,})\)\s*Tj", text)
    chunks.extend(re.findall(r"\(([^()]{3,})\)", text)[:300])
    cleaned = " ".join(_pdf_unescape(chunk) for chunk in chunks)
    return _compact_text(cleaned)


def _pdf_unescape(value: str) -> str:
    return value.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")


def _deterministic_validation(
    symbol: str,
    company_name: str,
    item: SourceItem,
    text: str,
    extraction_note: str,
) -> FilingValidation | None:
    lower = text.lower()
    positives = sorted(term for term in _POSITIVE_TERMS if term in lower)
    turnarounds = sorted(term for term in _TURNAROUND_TERMS if term in lower)
    capex = sorted(term for term in _CAPEX_TERMS if term in lower)
    red_flags = sorted(term for term in _RED_FLAG_TERMS if term in lower)
    growth_numbers = _extract_growth_numbers(text)

    if red_flags:
        return FilingValidation(
            symbol=symbol,
            company_name=company_name,
            category="red_flag",
            label="filing validation: " + ", ".join(red_flags[:3]),
            score=1.2,
            confidence=0.68,
            evidence=f"Local filing validation ({extraction_note}) found risk terms in {item.title[:180]}",
            source="Local filing validation",
            link=item.link,
            horizon="risk",
        )

    labels: list[str] = []
    if growth_numbers:
        labels.extend(growth_numbers[:3])
    if positives:
        labels.extend(positives[:3])
    if capex:
        labels.extend(capex[:3])
    if turnarounds:
        labels.extend(turnarounds[:2])
    if not labels:
        return None

    category = "filing_validation"
    horizon = "long"
    score = 1.0 + min(2.0, len(labels) * 0.25)
    confidence = 0.58
    if growth_numbers:
        score += 0.4
        confidence += 0.08
    if capex:
        score += 0.25
    if turnarounds:
        category = "turnaround"
        horizon = "turnaround"
        score += 0.4
    confidence = min(0.78, confidence)

    return FilingValidation(
        symbol=symbol,
        company_name=company_name,
        category=category,
        label="validated filing: " + ", ".join(labels[:5]),
        score=score,
        confidence=confidence,
        evidence=f"Local filing validation ({extraction_note}) found evidence in {item.title[:180]}",
        source="Local filing validation",
        link=item.link,
        horizon=horizon,
    )


def _extract_growth_numbers(text: str) -> list[str]:
    out: list[str] = []
    patterns = [
        (r"(revenue|sales)[^.]{0,80}?(\d+(?:\.\d+)?)\s*%", "revenue/sales"),
        (r"(pat|profit)[^.]{0,80}?(\d+(?:\.\d+)?)\s*%", "profit/PAT"),
        (r"(ebitda)[^.]{0,80}?(\d+(?:\.\d+)?)\s*%", "EBITDA"),
        (r"(margin)[^.]{0,80}?(\d+(?:\.\d+)?)\s*%", "margin"),
    ]
    lower = text.lower()
    for pattern, label in patterns:
        for match in re.finditer(pattern, lower):
            try:
                value = float(match.group(2))
            except ValueError:
                continue
            if value >= 10:
                out.append(f"{label} {value:.1f}%")
                break
    return out


def _ollama_validation(
    symbol: str,
    company_name: str,
    item: SourceItem,
    text: str,
) -> FilingValidation | None:
    model = os.getenv("OLLAMA_MODEL", "").strip()
    if not model or not text:
        return None
    prompt = (
        "Classify this Indian stock exchange filing for investment research. "
        "Return strict JSON with keys: materiality (low/medium/high), "
        "sentiment (negative/neutral/positive), reason (short). "
        "Do not invent numbers.\n\n"
        f"Company: {company_name} ({symbol})\n"
        f"Filing: {item.title}\n"
        f"Text:\n{text[:3500]}"
    )
    try:
        payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        response_text = data.get("response", "")
        parsed = _parse_jsonish(response_text)
    except Exception:
        return None

    materiality = str(parsed.get("materiality", "")).lower()
    sentiment = str(parsed.get("sentiment", "")).lower()
    reason = str(parsed.get("reason", "")).strip()
    if materiality not in {"medium", "high"} or sentiment != "positive":
        return None
    return FilingValidation(
        symbol=symbol,
        company_name=company_name,
        category="filing_validation",
        label=f"ollama validated: {materiality} materiality, {sentiment}",
        score=1.2 if materiality == "medium" else 1.6,
        confidence=0.56,
        evidence=f"Ollama filing validation: {reason[:180]}",
        source=f"Ollama filing validation ({model})",
        link=item.link,
        horizon="long",
    )


def _parse_jsonish(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def _to_signal(validation: FilingValidation) -> Signal:
    return Signal(
        symbol=validation.symbol,
        company_name=validation.company_name,
        category=validation.category,
        label=validation.label,
        score=validation.score,
        confidence=validation.confidence,
        evidence=validation.evidence,
        source=validation.source,
        link=validation.link,
        horizon=validation.horizon,
    )


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()
