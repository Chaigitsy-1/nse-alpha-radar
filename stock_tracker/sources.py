from __future__ import annotations

import csv
import html
import io
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
import zipfile
import io as _io

from .config import RuntimeConfig, project_path
from .models import SourceItem


USER_AGENT = "Mozilla/5.0 IndianStockTracker/0.1"


def _fetch_text(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _fetch_json(url: str, timeout: int = 20):
    return json.loads(_fetch_text(url, timeout=timeout))


def _clean(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def load_nse_index_closes(config: RuntimeConfig, report_date: date) -> tuple[dict[str, dict[str, float]], list[str]]:
    """
    Loads index close snapshot from NSE archives for the given date.
    Source: https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv
    """
    errors: list[str] = []
    # NSE index close file is present only on trading days. If the requested date is a holiday/weekend,
    # fall back to the most recent prior trading day file (up to 7 days back).
    text = ""
    used_date = report_date
    last_exc: Exception | None = None
    for back in range(0, 8):
        candidate = report_date if back == 0 else (report_date - timedelta(days=back))
        ddmmyyyy = candidate.strftime("%d%m%Y")
        url = f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"
        try:
            text = _fetch_text(url)
            used_date = candidate
            break
        except Exception as exc:
            last_exc = exc
            continue
    if not text:
        return {}, [f"NSE index close: failed for {report_date.isoformat()} (last error: {last_exc})"]

    rows = csv.DictReader(io.StringIO(text))
    index_stats: dict[str, dict[str, float]] = {}
    for row in rows:
        name = (row.get("Index Name") or "").strip()
        close = (row.get("Closing Index Value") or "").strip()
        if not name or not close:
            continue
        try:
            close_value = float(close.replace(",", ""))
        except ValueError:
            continue
        pct_raw = (row.get("Change(%)") or "").strip()
        try:
            pct_value = float(pct_raw.replace(",", "")) if pct_raw else 0.0
        except ValueError:
            pct_value = 0.0
        index_stats[name] = {"close": close_value, "pct_change": pct_value}
    if used_date != report_date:
        errors.append(f"NSE index close used fallback date {used_date.isoformat()} for report date {report_date.isoformat()}.")
    return index_stats, errors


def load_bse_rss(config: RuntimeConfig) -> tuple[list[SourceItem], list[str]]:
    items: list[SourceItem] = []
    errors: list[str] = []
    for url in config.settings["sources"]["bse_rss_candidates"]:
        try:
            xml_text = _fetch_text(url)
            root = ET.fromstring(xml_text)
            for node in root.findall(".//item"):
                items.append(
                    SourceItem(
                        source="BSE RSS",
                        title=_clean(node.findtext("title")),
                        link=_clean(node.findtext("link")),
                        published=_clean(node.findtext("pubDate")),
                        summary=_clean(node.findtext("description")),
                    )
                )
            if items:
                break
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    return items, errors


def load_nse_corporate_items(config: RuntimeConfig) -> tuple[list[SourceItem], list[str]]:
    items: list[SourceItem] = []
    errors: list[str] = []
    api = config.settings["sources"]["nse_api"]

    endpoint_specs = [
        ("NSE Announcements", api["announcements"], _nse_announcement_item),
        ("NSE Board Meetings", api["board_meetings"], _nse_board_meeting_item),
        ("NSE Corporate Actions", api["corporate_actions"], _nse_corporate_action_item),
    ]

    for source_name, url, mapper in endpoint_specs:
        try:
            data = _fetch_json(url)
            if isinstance(data, dict):
                data = data.get("data", [])
            for row in data:
                item = mapper(source_name, row)
                if item:
                    items.append(item)
        except Exception as exc:
            errors.append(f"{source_name} {url}: {exc}")
    return items, errors


def load_nse_corporate_items_for_date(
    config: RuntimeConfig,
    report_date: date,
) -> tuple[list[SourceItem], list[str]]:
    items: list[SourceItem] = []
    errors: list[str] = []
    api = config.settings["sources"]["nse_api"]

    date_text = report_date.strftime("%d-%m-%Y")

    endpoint_specs = [
        ("NSE Announcements", api["announcements"], _nse_announcement_item),
        ("NSE Board Meetings", api["board_meetings"], _nse_board_meeting_item),
        ("NSE Corporate Actions", api["corporate_actions"], _nse_corporate_action_item),
    ]

    for source_name, base_url, mapper in endpoint_specs:
        try:
            parsed = urllib.parse.urlsplit(base_url)
            query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
            query.update({"from_date": date_text, "to_date": date_text})
            url = urllib.parse.urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment)
            )

            data = _fetch_json(url)
            if isinstance(data, dict):
                data = data.get("data", [])
            for row in data:
                item = mapper(source_name, row)
                if item:
                    items.append(item)
        except Exception as exc:
            errors.append(f"{source_name} {base_url}: {exc}")
    return items, errors


def load_nse_market_snapshot(config: RuntimeConfig) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    errors: list[str] = []
    for index_name in config.settings["sources"]["nse_api"]["market_indices"]:
        url = "https://www.nseindia.com/api/equity-stockIndices?index=" + urllib.parse.quote(index_name)
        try:
            data = _fetch_json(url)
            for row in data.get("data", []):
                symbol = str(row.get("symbol", "")).upper()
                if not symbol or symbol == index_name:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "price_change_pct": str(row.get("pChange", 0) or 0),
                        "volume_multiple": "0",
                        "delivery_multiple": "0",
                        "last_price": str(row.get("lastPrice", "")),
                        "total_traded_volume": str(row.get("totalTradedVolume", "")),
                        "source": f"NSE market snapshot {index_name}",
                    }
                )
        except Exception as exc:
            errors.append(f"NSE market snapshot {index_name}: {exc}")
    return rows, errors


def load_nse_market_snapshot_for_date(
    config: RuntimeConfig,
    report_date: date,
) -> tuple[list[dict[str, str]], list[str]]:
    """
    Returns price/volume rows for the given report date.

    NSE "equity-stockIndices" endpoint is real-time and does not support historical queries.
    For non-today dates we intentionally return no rows to avoid data leakage in backtests.

    Historical price moves should be sourced from bhavcopy-based logic instead.
    """
    if report_date == date.today():
        return load_nse_market_snapshot(config)
    rows, errors = _load_nse_bhavcopy_price_moves(report_date)
    if rows:
        return rows, errors
    errors.append(
        f"NSE market snapshot skipped for backtest date {report_date.isoformat()} (real-time only) and bhavcopy unavailable."
    )
    return [], errors


def _bhavcopy_url(d: date) -> str:
    return f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def _download_bytes(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _bhavcopy_cache_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "cache"


def _get_cached_bhavcopy_csv_text(d: date) -> tuple[str | None, list[str]]:
    cache_dir = _bhavcopy_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"bhavcopy_cm_{d:%Y%m%d}.csv"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace"), []

    url = _bhavcopy_url(d)
    try:
        payload = _download_bytes(url)
    except Exception as exc:
        return None, [f"NSE bhavcopy {url}: {exc}"]

    try:
        with zipfile.ZipFile(_io.BytesIO(payload)) as zf:
            csv_names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                return None, [f"NSE bhavcopy {url}: no CSV inside zip"]
            text = zf.read(csv_names[0]).decode("utf-8", errors="replace")
            cache_path.write_text(text, encoding="utf-8")
            return text, []
    except Exception as exc:
        return None, [f"NSE bhavcopy {url}: {exc}"]


def _load_bhavcopy_close_volume(d: date) -> tuple[dict[str, tuple[float, float]], list[str]]:
    """
    Returns symbol -> (close, volume) for EQ series for the given trading day.
    """
    errors: list[str] = []
    text, errors = _get_cached_bhavcopy_csv_text(d)
    if not text:
        return {}, errors

    reader = csv.DictReader(_io.StringIO(text))
    fieldnames = {name.strip() for name in (reader.fieldnames or [])}
    is_legacy = "SYMBOL" in fieldnames

    out: dict[str, tuple[float, float]] = {}
    for row in reader:
        if is_legacy:
            if (row.get("SERIES") or "").strip().upper() != "EQ":
                continue
            symbol = (row.get("SYMBOL") or "").strip().upper()
            close = str(row.get("CLOSE") or "").strip().replace(",", "")
            vol = str(row.get("TOTTRDQTY") or row.get("TOTTRDVAL") or "").strip().replace(",", "")
        else:
            if (row.get("SctySrs") or "").strip().upper() != "EQ":
                continue
            symbol = (row.get("TckrSymb") or "").strip().upper()
            close = str(row.get("ClsPric") or "").strip().replace(",", "")
            vol = str(row.get("TtlTradgVol") or "").strip().replace(",", "")
        if not symbol or not close:
            continue
        try:
            close_v = float(close)
        except ValueError:
            continue
        try:
            vol_v = float(vol) if vol else 0.0
        except ValueError:
            vol_v = 0.0
        out[symbol] = (close_v, vol_v)
    return out, errors


def _rewind_trading_days_bhavcopy(
    start_day: date,
    trading_days: int,
    max_calendar_days: int = 260,
) -> date | None:
    current = start_day
    rewound = 0
    for _ in range(max_calendar_days):
        current = current - timedelta(days=1)
        data, _errors = _load_bhavcopy_close_volume(current)
        if not data:
            continue
        rewound += 1
        if rewound >= trading_days:
            return current
    return None


def load_bhavcopy_trend_rows(
    report_date: date,
    symbols: set[str],
) -> tuple[list[dict[str, str]], list[str]]:
    """
    Builds additional numeric features from bhavcopy for trend/momentum scoring.
    """
    errors: list[str] = []
    rows: list[dict[str, str]] = []

    d0, today_map, err0 = _nearest_bhavcopy_date(report_date, back_days=10)
    errors.extend(err0)
    if not d0:
        return [], errors

    # Prior trading dates for returns.
    d_5 = _rewind_trading_days_bhavcopy(d0, trading_days=5)
    d_20 = _rewind_trading_days_bhavcopy(d0, trading_days=20)
    d_60 = _rewind_trading_days_bhavcopy(d0, trading_days=60)

    map_5 = _load_bhavcopy_close_volume(d_5)[0] if d_5 else {}
    map_20 = _load_bhavcopy_close_volume(d_20)[0] if d_20 else {}
    map_60 = _load_bhavcopy_close_volume(d_60)[0] if d_60 else {}

    return_metrics: dict[str, tuple[float, float, float]] = {}
    for symbol in symbols:
        symbol_u = symbol.upper()
        cur = today_map.get(symbol_u)
        if not cur:
            continue
        cur_close = cur[0]

        def ret(prev_map: dict[str, tuple[float, float]]) -> float:
            prev = prev_map.get(symbol_u)
            if not prev or prev[0] <= 0:
                return 0.0
            return (cur_close - prev[0]) / prev[0] * 100.0

        ret_5 = ret(map_5) if map_5 else 0.0
        ret_20 = ret(map_20) if map_20 else 0.0
        ret_60 = ret(map_60) if map_60 else 0.0
        return_metrics[symbol_u] = (ret_5, ret_20, ret_60)

    ret20_percentiles = _percentile_ranks({symbol: values[1] for symbol, values in return_metrics.items()})
    ret60_percentiles = _percentile_ranks({symbol: values[2] for symbol, values in return_metrics.items()})

    for symbol_u, (ret_5, ret_20, ret_60) in return_metrics.items():
        rows.append(
            {
                "symbol": symbol_u,
                "ret_5d_pct": f"{ret_5:.4f}" if map_5 else "",
                "ret_20d_pct": f"{ret_20:.4f}" if map_20 else "",
                "ret_60d_pct": f"{ret_60:.4f}" if map_60 else "",
                "ret_20d_percentile": f"{ret20_percentiles.get(symbol_u, 0.0):.2f}",
                "ret_60d_percentile": f"{ret60_percentiles.get(symbol_u, 0.0):.2f}",
                "source": f"NSE bhavcopy trend {d0.isoformat()}",
            }
        )

    if d0 != report_date:
        errors.append(f"NSE bhavcopy fallback used {d0.isoformat()} for report date {report_date.isoformat()}.")
    return rows, errors


def _percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1])
    if len(ordered) == 1:
        return {ordered[0][0]: 100.0}
    ranks: dict[str, float] = {}
    denominator = len(ordered) - 1
    for idx, (symbol, _value) in enumerate(ordered):
        ranks[symbol] = idx / denominator * 100.0
    return ranks


def _nearest_bhavcopy_date(target: date, back_days: int = 10) -> tuple[date | None, dict[str, tuple[float, float]], list[str]]:
    all_errors: list[str] = []
    for back in range(0, back_days + 1):
        candidate = target - timedelta(days=back)
        data, errors = _load_bhavcopy_close_volume(candidate)
        all_errors.extend(errors)
        if data:
            return candidate, data, all_errors
    return None, {}, all_errors


def _load_nse_bhavcopy_price_moves(report_date: date) -> tuple[list[dict[str, str]], list[str]]:
    """
    Builds rows compatible with classify_price_volume() for the report date based on bhavcopy.
    """
    rows: list[dict[str, str]] = []
    d0, today_map, errors = _nearest_bhavcopy_date(report_date, back_days=10)
    if not d0:
        return [], errors
    d1, prev_map, errors2 = _nearest_bhavcopy_date(d0 - timedelta(days=1), back_days=10)
    errors.extend(errors2)
    if not d1:
        return [], errors

    for symbol, (close, vol) in today_map.items():
        prev = prev_map.get(symbol)
        if not prev:
            continue
        prev_close = prev[0]
        if prev_close <= 0:
            continue
        pct = (close - prev_close) / prev_close * 100.0
        rows.append(
            {
                "symbol": symbol,
                "price_change_pct": f"{pct:.4f}",
                "volume_multiple": "0",
                "delivery_multiple": "0",
                "last_price": f"{close:.2f}",
                "total_traded_volume": f"{vol:.0f}",
                "source": f"NSE bhavcopy {d0.isoformat()}",
            }
        )
    if d0 != report_date:
        errors.append(f"NSE bhavcopy fallback used {d0.isoformat()} for report date {report_date.isoformat()}.")
    return rows, errors


def load_manual_events(config: RuntimeConfig) -> list[SourceItem]:
    path = project_path(config, config.settings["sources"]["local_files"]["manual_events"])
    if not path.exists():
        return []
    rows = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8")))
    items: list[SourceItem] = []
    for row in rows:
        items.append(
            SourceItem(
                source=row.get("source", "Manual Event"),
                title=row.get("title", ""),
                link=row.get("link", ""),
                published=row.get("published", ""),
                summary=row.get("summary", ""),
                symbol_hint=row.get("symbol", ""),
            )
        )
    return items


def load_optional_csv(config: RuntimeConfig, key: str) -> list[dict[str, str]]:
    path = Path(project_path(config, config.settings["sources"]["local_files"][key]))
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(handle)]


def _nse_announcement_item(source_name: str, row: dict) -> SourceItem:
    symbol = str(row.get("symbol", "")).upper()
    company = row.get("sm_name", "")
    desc = row.get("desc", "")
    text = row.get("attchmntText", "")
    return SourceItem(
        source=source_name,
        title=f"{company} - {desc}".strip(" -"),
        link=row.get("attchmntFile", ""),
        published=row.get("an_dt", "") or row.get("sort_date", ""),
        summary=text,
        symbol_hint=symbol,
    )


def _nse_board_meeting_item(source_name: str, row: dict) -> SourceItem:
    symbol = str(row.get("bm_symbol", "")).upper()
    company = row.get("sm_name", "") or symbol
    purpose = row.get("bm_purpose", "")
    desc = row.get("bm_desc", "")
    return SourceItem(
        source=source_name,
        title=f"{company} board meeting - {purpose}".strip(" -"),
        link=row.get("attachment", ""),
        published=row.get("bm_date", ""),
        summary=desc,
        symbol_hint=symbol,
    )


def _nse_corporate_action_item(source_name: str, row: dict) -> SourceItem:
    symbol = str(row.get("symbol", "")).upper()
    subject = row.get("subject", "")
    return SourceItem(
        source=source_name,
        title=f"{symbol} corporate action - {subject}".strip(" -"),
        link="",
        published=row.get("exDate", ""),
        summary=" ".join(str(row.get(key, "")) for key in ["comp", "purpose", "subject"] if row.get(key)),
        symbol_hint=symbol,
    )
