from __future__ import annotations

import argparse
import csv
import io
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path


REPORT_NAME_RE = re.compile(r"stock_report_(\d{4}-\d{2}-\d{2})\.md$")


@dataclass(frozen=True)
class ExtractedSignal:
    category: str
    label: str
    confidence: float
    evidence: str
    source: str
    link: str
    horizon: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class ExtractedOpportunity:
    report_date: date
    symbol: str
    company_name: str
    bucket: str
    quality_tier: str
    bucket_score: float
    total: float
    risk: float
    signals: tuple[ExtractedSignal, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backdated backtest + price validation")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--horizons", default="90,180", help="Comma-separated forward days (default: 90,180)")
    parser.add_argument("--max_per_day", type=int, default=10, help="Max opportunities per report date (Top Overall)")
    parser.add_argument("--out", default="", help="Output CSV path (default: reports/backdated_test_*.csv)")
    parser.add_argument("--report_dir", default="reports", help="Directory containing markdown reports")
    return parser.parse_args()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _safe_float(value: str) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _nearest_trading_date(
    symbol: str,
    target: date,
    *,
    direction: str,
    cache_dir: Path,
    close_cache: dict[date, dict[str, float]] | None = None,
    max_days: int = 10,
) -> tuple[date | None, float | None, str | None]:
    step = 1 if direction == "next" else -1
    current = target
    close_cache = close_cache or {}
    for _ in range(max_days):
        close = _get_close_from_bhavcopy(symbol, current, cache_dir=cache_dir, close_cache=close_cache)
        if close is not None:
            return current, close, "bhavcopy"
        current = current + timedelta(days=step)
    return None, None, None


def _next_trading_date(
    d: date,
    *,
    cache_dir: Path,
    index_cache: dict[date, dict[str, float]],
    max_days: int = 10,
) -> date | None:
    current = d
    for _ in range(max_days):
        current = current + timedelta(days=1)
        if _get_index_close_map_cached(current, cache_dir=cache_dir, index_cache=index_cache):
            return current
    return None


def _get_index_close_map_cached(
    d: date,
    *,
    cache_dir: Path,
    index_cache: dict[date, dict[str, float]],
) -> dict[str, float]:
    if d not in index_cache:
        index_cache[d] = _get_index_close_map(d, cache_dir=cache_dir)
    return index_cache[d]


def _advance_trading_days(
    start_day: date,
    trading_days: int,
    *,
    cache_dir: Path,
    index_cache: dict[date, dict[str, float]],
    max_calendar_days: int = 260,
) -> date | None:
    current = start_day
    advanced = 0
    for _ in range(max_calendar_days):
        nxt = _next_trading_date(current, cache_dir=cache_dir, index_cache=index_cache, max_days=7)
        if not nxt:
            return None
        current = nxt
        advanced += 1
        if advanced >= trading_days:
            return current
    return None


def _bhavcopy_url(d: date) -> str:
    # Observed working format:
    # https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
    return f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def _download_bytes(url: str, *, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _get_bhavcopy_csv_text(d: date, *, cache_dir: Path) -> str | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"bhavcopy_cm_{d:%Y%m%d}.csv"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace")

    url = _bhavcopy_url(d)
    try:
        payload = _download_bytes(url)
    except Exception:
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            # Typically one CSV inside.
            csv_names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                return None
            text = zf.read(csv_names[0]).decode("utf-8", errors="replace")
            cache_path.write_text(text, encoding="utf-8")
            return text
    except Exception:
        return None


def _load_bhavcopy_close_map(d: date, *, cache_dir: Path) -> dict[str, float]:
    text = _get_bhavcopy_csv_text(d, cache_dir=cache_dir)
    if not text:
        return {}
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = {name.strip() for name in (reader.fieldnames or [])}
    is_legacy = "SYMBOL" in fieldnames

    closes: dict[str, float] = {}
    for row in reader:
        if is_legacy:
            if (row.get("SERIES") or "").strip().upper() != "EQ":
                continue
            symbol = (row.get("SYMBOL") or "").strip().upper()
            if not symbol:
                continue
            close = _safe_float((row.get("CLOSE") or "0").strip().replace(",", ""))
            closes[symbol] = close
            continue

        if (row.get("SctySrs") or "").strip().upper() != "EQ":
            continue
        symbol = (row.get("TckrSymb") or "").strip().upper()
        if not symbol:
            continue
        close = _safe_float((row.get("ClsPric") or "0").strip().replace(",", ""))
        closes[symbol] = close
    return closes


def _get_close_from_bhavcopy(
    symbol: str,
    d: date,
    *,
    cache_dir: Path,
    close_cache: dict[date, dict[str, float]],
) -> float | None:
    if d not in close_cache:
        close_cache[d] = _load_bhavcopy_close_map(d, cache_dir=cache_dir)
    return close_cache[d].get(symbol.upper())


def _index_close_url(d: date) -> str:
    # https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv
    return f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv"


def _get_index_close_map(d: date, *, cache_dir: Path) -> dict[str, float]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"ind_close_all_{d:%d%m%Y}.csv"
    if cache_path.exists():
        text = cache_path.read_text(encoding="utf-8", errors="replace")
    else:
        try:
            payload = _download_bytes(_index_close_url(d))
        except Exception:
            return {}
        text = payload.decode("utf-8", errors="replace")
        cache_path.write_text(text, encoding="utf-8")

    rows = csv.DictReader(io.StringIO(text))
    closes: dict[str, float] = {}
    for row in rows:
        name = (row.get("Index Name") or "").strip()
        close = (row.get("Closing Index Value") or "").strip()
        if not name or not close:
            continue
        closes[name] = _safe_float(close.replace(",", ""))
    return closes


def _get_index_close(
    index_name: str,
    d: date,
    *,
    cache_dir: Path,
    index_cache: dict[date, dict[str, float]],
) -> float | None:
    return _get_index_close_map_cached(d, cache_dir=cache_dir, index_cache=index_cache).get(index_name)


def _reason_from_signal(signal: ExtractedSignal) -> str:
    category = signal.category
    label = signal.label.lower()
    if category == "technical_volume":
        return "Price/volume confirmation suggests near-term momentum continuation."
    if category == "short_term_trigger":
        if any(k in label for k in ["results", "outcome"]):
            return "Earnings/results catalyst can trigger re-rating and momentum."
        if "order win" in label or "contract" in label or "loa" in label:
            return "Order/catalyst may improve visibility; market may re-rate."
        if any(k in label for k in ["dividend", "record date", "board meeting"]):
            return "Corporate action calendar event; often noisy unless paired with price/fundamental follow-through."
        return "Corporate event catalyst may drive short-term attention and momentum."
    if category == "management_guidance":
        return "Management guidance/presentation can shift expectations; medium-term re-rating potential."
    if category == "long_term_tailwind":
        return "Theme/tailwind alignment may support multi-quarter growth expectations."
    if category == "capex_lifecycle":
        return "Capex lifecycle is connecting capacity, execution, demand, financial follow-through, or market confirmation."
    if category == "turnaround":
        return "Turnaround signal suggests improving fundamentals; higher-risk but higher-upside if confirmed."
    if category == "financial_quality":
        return "Quality improvement (margins/cashflow/deleveraging) supports sustained compounding thesis."
    if category == "red_flag":
        return "Risk/negative disclosure can cap upside or cause drawdowns."
    return "Heuristic signal match based on filings/keywords."


def _parse_opportunities_from_report(path: Path, *, max_per_day: int) -> list[ExtractedOpportunity]:
    report_date = _date_from_report_path(path)
    if not report_date:
        return []

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    opportunities: list[ExtractedOpportunity] = []

    current_section: str | None = None
    current_symbol: str | None = None
    current_company: str | None = None
    current_bucket_score: float | None = None
    current_total: float | None = None
    current_risk: float | None = None
    current_quality_tier: str = ""
    current_signals: list[ExtractedSignal] = []
    current_rank: int | None = None

    def flush() -> None:
        nonlocal current_symbol, current_company, current_bucket_score, current_total, current_risk, current_quality_tier, current_signals, current_rank
        if (
            current_section == "Actionable Today"
            and current_symbol
            and current_company
            and current_bucket_score is not None
            and current_total is not None
            and current_risk is not None
        ):
            opportunities.append(
                ExtractedOpportunity(
                    report_date=report_date,
                    symbol=current_symbol,
                    company_name=current_company,
                    bucket=current_section,
                    quality_tier=current_quality_tier or "Unrated",
                    bucket_score=current_bucket_score,
                    total=current_total,
                    risk=current_risk,
                    signals=tuple(current_signals[:4]),
                )
            )
        current_symbol = None
        current_company = None
        current_bucket_score = None
        current_total = None
        current_risk = None
        current_quality_tier = ""
        current_signals = []
        current_rank = None

    section_re = re.compile(r"^##\s+(.+)$")
    company_re = re.compile(r"^###\s+(\d+)\.\s+(.*)\s+\(([^)]+)\)\s*$")
    quality_re = re.compile(r"^- Quality Tier:\s+\*\*(.+?)\*\*")
    score_re = re.compile(r"^- Score:\s+([0-9.]+)\s+\|\s+Total:\s+([0-9.\-]+)\s+\|\s+Risk:\s+([0-9.]+)\s*$")

    # Old signal line:
    # - short_term_trigger: board meeting, results | confidence 0.61 | [NSE ...](...) | Evidence
    old_sig_re = re.compile(
        r"^-\s+([^:]+):\s+(.+?)\s+\|\s+confidence\s+([0-9.]+)\s+\|\s*(.+?)\s*\|\s*(.+)\s*$"
    )
    # New signal line:
    # - category: label | horizon short | contrib 1.85 (= 2.40x0.77) | [src](link) | evidence
    new_sig_re = re.compile(
        r"^-\s+([^:]+):\s+(.+?)\s+\|\s+horizon\s+(\w+)\s+\|\s+contrib\s+([0-9.\-]+)\s+\(=\s+([0-9.\-]+)x([0-9.]+)\)\s+\|\s*(.+?)\s*\|\s*(.+)\s*$"
    )

    def parse_source_blob(blob: str) -> tuple[str, str]:
        blob = blob.strip()
        m = re.match(r"^\[([^\]]+)\]\(([^)]+)\)$", blob)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return blob, ""

    for line in lines:
        sec = section_re.match(line)
        if sec:
            flush()
            current_section = sec.group(1).strip()
            continue

        comp = company_re.match(line)
        if comp:
            flush()
            current_rank = int(comp.group(1))
            current_company = comp.group(2).strip()
            current_symbol = comp.group(3).strip().upper()
            continue

        if current_section != "Actionable Today":
            continue

        if current_rank is not None and len([o for o in opportunities if o.report_date == report_date]) >= max_per_day:
            continue

        quality = quality_re.match(line)
        if quality:
            current_quality_tier = quality.group(1).strip()
            continue

        sc = score_re.match(line)
        if sc:
            current_bucket_score = _safe_float(sc.group(1))
            current_total = _safe_float(sc.group(2))
            current_risk = _safe_float(sc.group(3))
            continue

        ns = new_sig_re.match(line)
        if ns:
            category = ns.group(1).strip()
            label = ns.group(2).strip()
            horizon = ns.group(3).strip()
            score_val = _safe_float(ns.group(5))
            confidence = _safe_float(ns.group(6))
            source_blob = ns.group(7).strip()
            evidence = ns.group(8).strip()
            source, link = parse_source_blob(source_blob)
            current_signals.append(
                ExtractedSignal(
                    category=category,
                    label=label,
                    confidence=confidence,
                    evidence=evidence,
                    source=source,
                    link=link,
                    horizon=horizon,
                    score=score_val,
                )
            )
            continue

        os = old_sig_re.match(line)
        if os:
            category = os.group(1).strip()
            label = os.group(2).strip()
            confidence = _safe_float(os.group(3))
            source_blob = os.group(4).strip()
            evidence = os.group(5).strip()
            source, link = parse_source_blob(source_blob)
            current_signals.append(
                ExtractedSignal(
                    category=category,
                    label=label,
                    confidence=confidence,
                    evidence=evidence,
                    source=source,
                    link=link,
                )
            )
            continue

    flush()
    return opportunities[:max_per_day]


def _date_from_report_path(path: Path) -> date | None:
    match = REPORT_NAME_RE.search(path.name)
    if not match:
        return None
    return _parse_date(match.group(1))


def _pct_change(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return (b - a) / a * 100.0


def run_backdated_test(
    *,
    start: date,
    end: date,
    horizons: list[int],
    max_per_day: int,
    report_dir: Path,
    out_path: Path,
) -> Path:
    cache_dir = report_dir.parent / "data" / "cache"
    close_cache: dict[date, dict[str, float]] = {}
    index_cache: dict[date, dict[str, float]] = {}
    index_name_midcap = "Nifty Midcap 150"
    index_name_smallcap = "Nifty Smallcap 250"

    report_files = sorted(report_dir.glob("stock_report_*.md"))
    opportunities: list[ExtractedOpportunity] = []
    for path in report_files:
        report_date = _date_from_report_path(path)
        if not report_date:
            continue
        if report_date < start or report_date > end:
            continue
        opportunities.extend(_parse_opportunities_from_report(path, max_per_day=max_per_day))

    latest_date, _latest_close, _ = _nearest_trading_date(
        "RELIANCE",
        date.today(),
        direction="prev",
        cache_dir=cache_dir,
        close_cache=close_cache,
        max_days=10,
    )
    # latest_date may be None; we still proceed per-symbol.

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Fixed trading-day horizons (~1/3/6 months) for proper backtesting.
    trading_day_horizons = [21, 63, 126]

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "trigger_date",
                "symbol",
                "company_name",
                "bucket",
                "quality_tier",
                "bucket_score",
                "total_score",
                "risk_score",
                "top_signal_categories",
                "top_signal_labels",
                "top_confidences",
                "opportunity_thesis",
                "price_trigger_date",
                "price_latest_date",
                "price_latest",
                "return_to_latest_pct",
                "midcap_close_trigger",
                "midcap_close_latest",
                "midcap_return_to_latest_pct",
                "alpha_vs_midcap_to_latest_pct",
                "smallcap_close_trigger",
                "smallcap_close_latest",
                "smallcap_return_to_latest_pct",
                "alpha_vs_smallcap_to_latest_pct",
                "macro_regime_tag",
                "macro_event_notes",
                *[f"price_plus_{h}d" for h in horizons],
                *[f"return_plus_{h}d_pct" for h in horizons],
                *[f"return_plus_{h}d_or_to_date_pct" for h in horizons],
                *[f"price_plus_{h}td" for h in trading_day_horizons],
                *[f"return_plus_{h}td_pct" for h in trading_day_horizons],
                *[f"midcap_return_plus_{h}td_pct" for h in trading_day_horizons],
                *[f"alpha_vs_midcap_plus_{h}td_pct" for h in trading_day_horizons],
                "validation_notes",
            ],
        )
        writer.writeheader()

        macro_events = _load_macro_events(report_dir.parent / "data" / "macro_events.csv")

        for opp in opportunities:
            trigger_d = opp.report_date
            trigger_trade_date = trigger_d
            trigger_close = _get_close_from_bhavcopy(
                opp.symbol,
                trigger_trade_date,
                cache_dir=cache_dir,
                close_cache=close_cache,
            )
            if trigger_close is None:
                trigger_trade_date, trigger_close, _ = _nearest_trading_date(
                    opp.symbol,
                    trigger_d,
                    direction="prev",
                    cache_dir=cache_dir,
                    close_cache=close_cache,
                    max_days=10,
                )
                if trigger_trade_date:
                    trigger_close = _get_close_from_bhavcopy(
                        opp.symbol,
                        trigger_trade_date,
                        cache_dir=cache_dir,
                        close_cache=close_cache,
                    )

            if latest_date:
                latest_trade_date, latest_close, _ = _nearest_trading_date(
                    opp.symbol,
                    latest_date,
                    direction="prev",
                    cache_dir=cache_dir,
                    close_cache=close_cache,
                    max_days=10,
                )
                if latest_trade_date:
                    latest_close = _get_close_from_bhavcopy(
                        opp.symbol,
                        latest_trade_date,
                        cache_dir=cache_dir,
                        close_cache=close_cache,
                    )
            else:
                latest_trade_date, latest_close = None, None

            forward_prices: dict[int, float | None] = {}
            forward_returns: dict[int, float | None] = {}
            forward_returns_or_to_date: dict[int, float | None] = {}
            for h in horizons:
                target = trigger_d + timedelta(days=h)
                forward_trade_date, forward_close, _ = _nearest_trading_date(
                    opp.symbol,
                    target,
                    direction="next",
                    cache_dir=cache_dir,
                    close_cache=close_cache,
                    max_days=10,
                )
                if forward_trade_date:
                    forward_close = _get_close_from_bhavcopy(
                        opp.symbol,
                        forward_trade_date,
                        cache_dir=cache_dir,
                        close_cache=close_cache,
                    )
                forward_prices[h] = forward_close
                forward_returns[h] = _pct_change(trigger_close, forward_close)
                if forward_close is not None:
                    forward_returns_or_to_date[h] = forward_returns[h]
                else:
                    forward_returns_or_to_date[h] = _pct_change(trigger_close, latest_close)

            # Proper fixed-horizon backtest (trading days, benchmarked).
            td_prices: dict[int, float | None] = {}
            td_returns: dict[int, float | None] = {}
            td_midcap_returns: dict[int, float | None] = {}
            td_alpha_midcap: dict[int, float | None] = {}
            trigger_index_date = trigger_trade_date or trigger_d
            midcap_trigger_td = _get_index_close(
                index_name_midcap, trigger_index_date, cache_dir=cache_dir, index_cache=index_cache
            )
            for td in trading_day_horizons:
                forward_td_date = _advance_trading_days(
                    trigger_index_date, td, cache_dir=cache_dir, index_cache=index_cache
                )
                if forward_td_date:
                    forward_td_close = _get_close_from_bhavcopy(
                        opp.symbol,
                        forward_td_date,
                        cache_dir=cache_dir,
                        close_cache=close_cache,
                    )
                    td_prices[td] = forward_td_close
                    td_returns[td] = _pct_change(trigger_close, forward_td_close)
                    midcap_forward_td = _get_index_close(
                        index_name_midcap, forward_td_date, cache_dir=cache_dir, index_cache=index_cache
                    )
                    td_midcap_returns[td] = _pct_change(midcap_trigger_td, midcap_forward_td)
                    if td_returns[td] is not None and td_midcap_returns[td] is not None:
                        td_alpha_midcap[td] = td_returns[td] - td_midcap_returns[td]
                    else:
                        td_alpha_midcap[td] = None
                else:
                    td_prices[td] = None
                    td_returns[td] = None
                    td_midcap_returns[td] = None
                    td_alpha_midcap[td] = None

            thesis = " ".join(_reason_from_signal(s) for s in opp.signals[:2]).strip()
            if not thesis:
                thesis = "Keyword-matched catalyst; requires manual review."

            confs = [s.confidence for s in opp.signals if s.confidence]
            top_categories = "; ".join(s.category for s in opp.signals)
            top_labels = "; ".join(s.label for s in opp.signals)
            top_confidences = "; ".join(f"{s.confidence:.2f}" for s in opp.signals)

            notes: list[str] = []
            if trigger_close is None:
                notes.append("Missing trigger-date bhavcopy close (symbol may differ vs NSE EQ).")
            if latest_close is None:
                notes.append("Missing latest bhavcopy close for symbol.")
            if opp.risk > 0:
                notes.append("Risk signals present; upside may be capped or delayed.")
            if all(s.category == "short_term_trigger" and "board meeting" in s.label.lower() for s in opp.signals):
                notes.append("Calendar-only catalyst; high false-positive rate without follow-through.")

            # Benchmark / macro regime tagging (index return on trigger date and to latest).
            midcap_trigger = _get_index_close(
                index_name_midcap, trigger_trade_date or trigger_d, cache_dir=cache_dir, index_cache=index_cache
            )
            midcap_latest = (
                _get_index_close(index_name_midcap, latest_trade_date, cache_dir=cache_dir, index_cache=index_cache)
                if latest_trade_date
                else None
            )
            smallcap_trigger = _get_index_close(
                index_name_smallcap, trigger_trade_date or trigger_d, cache_dir=cache_dir, index_cache=index_cache
            )
            smallcap_latest = (
                _get_index_close(
                    index_name_smallcap, latest_trade_date, cache_dir=cache_dir, index_cache=index_cache
                )
                if latest_trade_date
                else None
            )
            midcap_ret = _pct_change(midcap_trigger, midcap_latest)
            smallcap_ret = _pct_change(smallcap_trigger, smallcap_latest)
            stock_ret = _pct_change(trigger_close, latest_close)

            alpha_mid = (stock_ret - midcap_ret) if (stock_ret is not None and midcap_ret is not None) else None
            alpha_small = (
                (stock_ret - smallcap_ret) if (stock_ret is not None and smallcap_ret is not None) else None
            )

            regime_tag = _macro_regime_tag(
                midcap_trigger=midcap_trigger,
                midcap_prev=_get_index_close(
                    index_name_midcap,
                    (trigger_trade_date or trigger_d) - timedelta(days=1),
                    cache_dir=cache_dir,
                    index_cache=index_cache,
                ),
                smallcap_trigger=smallcap_trigger,
                smallcap_prev=_get_index_close(
                    index_name_smallcap,
                    (trigger_trade_date or trigger_d) - timedelta(days=1),
                    cache_dir=cache_dir,
                    index_cache=index_cache,
                ),
            )
            event_note = macro_events.get(trigger_d.isoformat(), "")

            writer.writerow(
                {
                    "trigger_date": trigger_d.isoformat(),
                    "symbol": opp.symbol,
                    "company_name": opp.company_name,
                    "bucket": opp.bucket,
                    "quality_tier": opp.quality_tier,
                    "bucket_score": f"{opp.bucket_score:.2f}",
                    "total_score": f"{opp.total:.2f}",
                    "risk_score": f"{opp.risk:.2f}",
                    "top_signal_categories": top_categories,
                    "top_signal_labels": top_labels,
                    "top_confidences": top_confidences,
                    "opportunity_thesis": thesis,
                    "price_trigger_date": trigger_trade_date.isoformat() if trigger_trade_date else "",
                    "price_latest_date": latest_trade_date.isoformat() if latest_trade_date else "",
                    "price_latest": f"{latest_close:.2f}" if latest_close is not None else "",
                    "return_to_latest_pct": f"{(_pct_change(trigger_close, latest_close) or 0.0):.2f}"
                    if (trigger_close is not None and latest_close is not None)
                    else "",
                    "midcap_close_trigger": f"{midcap_trigger:.2f}" if midcap_trigger is not None else "",
                    "midcap_close_latest": f"{midcap_latest:.2f}" if midcap_latest is not None else "",
                    "midcap_return_to_latest_pct": f"{(midcap_ret or 0.0):.2f}" if midcap_ret is not None else "",
                    "alpha_vs_midcap_to_latest_pct": f"{(alpha_mid or 0.0):.2f}" if alpha_mid is not None else "",
                    "smallcap_close_trigger": f"{smallcap_trigger:.2f}" if smallcap_trigger is not None else "",
                    "smallcap_close_latest": f"{smallcap_latest:.2f}" if smallcap_latest is not None else "",
                    "smallcap_return_to_latest_pct": f"{(smallcap_ret or 0.0):.2f}"
                    if smallcap_ret is not None
                    else "",
                    "alpha_vs_smallcap_to_latest_pct": f"{(alpha_small or 0.0):.2f}"
                    if alpha_small is not None
                    else "",
                    "macro_regime_tag": regime_tag,
                    "macro_event_notes": event_note,
                    **{f"price_plus_{h}d": f"{forward_prices[h]:.2f}" if forward_prices[h] is not None else "" for h in horizons},
                    **{
                        f"return_plus_{h}d_pct": f"{forward_returns[h]:.2f}" if forward_returns[h] is not None else ""
                        for h in horizons
                    },
                    **{
                        f"return_plus_{h}d_or_to_date_pct": f"{forward_returns_or_to_date[h]:.2f}"
                        if forward_returns_or_to_date[h] is not None
                        else ""
                        for h in horizons
                    },
                    **{f"price_plus_{td}td": f"{td_prices[td]:.2f}" if td_prices[td] is not None else "" for td in trading_day_horizons},
                    **{
                        f"return_plus_{td}td_pct": f"{td_returns[td]:.2f}" if td_returns[td] is not None else ""
                        for td in trading_day_horizons
                    },
                    **{
                        f"midcap_return_plus_{td}td_pct": f"{td_midcap_returns[td]:.2f}"
                        if td_midcap_returns[td] is not None
                        else ""
                        for td in trading_day_horizons
                    },
                    **{
                        f"alpha_vs_midcap_plus_{td}td_pct": f"{td_alpha_midcap[td]:.2f}"
                        if td_alpha_midcap[td] is not None
                        else ""
                        for td in trading_day_horizons
                    },
                    "validation_notes": " ".join(notes),
                }
            )

    return out_path


def _load_macro_events(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    rows = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace")))
    events: dict[str, str] = {}
    for row in rows:
        d = (row.get("date") or "").strip()
        event = (row.get("event") or "").strip()
        notes = (row.get("notes") or "").strip()
        if not d or not event:
            continue
        events[d] = f"{event}{(' - ' + notes) if notes else ''}"
    return events


def _macro_regime_tag(
    *,
    midcap_trigger: float | None,
    midcap_prev: float | None,
    smallcap_trigger: float | None,
    smallcap_prev: float | None,
) -> str:
    mid_ret = _pct_change(midcap_prev, midcap_trigger) if (midcap_prev and midcap_trigger) else None
    small_ret = _pct_change(smallcap_prev, smallcap_trigger) if (smallcap_prev and smallcap_trigger) else None
    rets = [r for r in [mid_ret, small_ret] if r is not None]
    if not rets:
        return ""
    avg = sum(rets) / len(rets)
    if avg <= -1.0:
        return "risk_off"
    if avg >= 1.0:
        return "risk_on"
    return "neutral"


def main() -> int:
    args = parse_args()
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    horizons = [int(part.strip()) for part in str(args.horizons).split(",") if part.strip()]

    report_dir = Path(args.report_dir)
    out_path = Path(args.out) if args.out else Path("reports") / f"backdated_test_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    output = run_backdated_test(
        start=start,
        end=end,
        horizons=horizons,
        max_per_day=args.max_per_day,
        report_dir=report_dir,
        out_path=out_path,
    )
    print(f"Backdated test written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
