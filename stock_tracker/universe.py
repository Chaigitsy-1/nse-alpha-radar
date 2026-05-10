from __future__ import annotations

import csv
import io
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile

from .config import RuntimeConfig, project_path
from .models import Company


USER_AGENT = "Mozilla/5.0 IndianStockTracker/0.1"


def _normalize_header(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _read_csv_text(text: str, index_name: str) -> list[Company]:
    rows = csv.DictReader(io.StringIO(text))
    companies: list[Company] = []
    for row in rows:
        normalized = {_normalize_header(k): (v or "").strip() for k, v in row.items()}
        symbol = (
            normalized.get("symbol")
            or normalized.get("nse_symbol")
            or normalized.get("ticker")
            or normalized.get("exchange_code")
            or ""
        ).upper()
        if not symbol:
            continue
        companies.append(
            Company(
                symbol=symbol,
                name=normalized.get("company_name") or normalized.get("name") or symbol,
                index=index_name,
                sector=normalized.get("sector", ""),
                industry=normalized.get("industry", ""),
                market_cap_cr=_parse_float(
                    normalized.get("market_cap_cr")
                    or normalized.get("marketcap_cr")
                    or normalized.get("market_cap")
                    or normalized.get("mcap_cr")
                ),
            )
        )
    return companies


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = str(value).strip().replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _download_csv(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _download_file(url: str, path: Path, timeout: int = 60) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.read())


def _load_index(config: RuntimeConfig, index_key: str, index_name: str) -> tuple[list[Company], list[str]]:
    warnings: list[str] = []
    local_file = project_path(config, config.settings["universe"]["local_files"][index_key])
    if local_file.exists():
        return _read_csv_text(local_file.read_text(encoding="utf-8"), index_name), warnings

    errors: list[str] = []
    for url in config.settings["universe"]["remote_urls"].get(index_key, []):
        try:
            return _read_csv_text(_download_csv(url), index_name), warnings
        except Exception as exc:  # Network sources can be flaky; report continues with fallback.
            errors.append(f"{url}: {exc}")

    fallback = _fallback_universe(index_key, index_name)
    if fallback:
        warnings.append(f"Universe fallback used for {index_name} via seed file.")
        return fallback, warnings

    warnings.append(
        f"Universe load failed for {index_name}. Missing local file: {local_file}. "
        f"Remote errors: {' | '.join(errors) if errors else 'no remote URLs configured'}"
    )
    return [], warnings


def _fallback_universe(index_key: str, index_name: str) -> list[Company]:
    fallback_path = Path(__file__).resolve().parents[1] / "data" / f"{index_key}_seed.csv"
    if fallback_path.exists():
        return _read_csv_text(fallback_path.read_text(encoding="utf-8"), index_name)
    return []


def _infer_symbols_from_csv(path: Path) -> set[str]:
    if not path.exists():
        return set()
    rows = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace")))
    symbols: set[str] = set()
    for row in rows:
        symbol = (row.get("symbol") or row.get("SYMBOL") or "").strip().upper()
        if symbol:
            symbols.add(symbol)
    return symbols


def _infer_universe_from_local_sources(config: RuntimeConfig) -> list[Company]:
    sources = config.settings.get("sources", {}).get("local_files", {})
    candidate_paths: list[Path] = []
    for key in ("manual_events", "price_volume", "fundamentals"):
        relative = sources.get(key)
        if relative:
            candidate_paths.append(project_path(config, relative))

    symbols: set[str] = set()
    for path in candidate_paths:
        symbols.update(_infer_symbols_from_csv(Path(path)))

    return [
        Company(symbol=symbol, name=symbol, index="Local snapshot", sector="", industry="")
        for symbol in sorted(symbols)
    ]


def _merge_companies(companies: Iterable[Company]) -> dict[str, Company]:
    by_symbol: dict[str, Company] = {}
    for company in companies:
        existing = by_symbol.get(company.symbol)
        if existing:
            by_symbol[company.symbol] = Company(
                symbol=company.symbol,
                name=company.name or existing.name,
                index=f"{existing.index}, {company.index}",
                sector=company.sector or existing.sector,
                industry=company.industry or existing.industry,
                market_cap_cr=company.market_cap_cr if company.market_cap_cr is not None else existing.market_cap_cr,
            )
        else:
            by_symbol[company.symbol] = company
    return by_symbol


def _xlsx_shared_strings(zf: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for item in root.findall("x:si", ns):
        out.append("".join(node.text or "" for node in item.findall(".//x:t", ns)))
    return out


def _xlsx_rows(path: Path) -> list[dict[str, str]]:
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(path) as zf:
        shared = _xlsx_shared_strings(zf)
        sheet_names = [name for name in zf.namelist() if name.startswith("xl/worksheets/sheet")]
        if not sheet_names:
            return []
        root = ET.fromstring(zf.read(sheet_names[0]))

        raw_rows: list[dict[str, str]] = []
        for row in root.findall(".//x:row", ns):
            values: dict[str, str] = {}
            for cell in row.findall("x:c", ns):
                ref = cell.attrib.get("r", "")
                col = "".join(ch for ch in ref if ch.isalpha())
                value_node = cell.find("x:v", ns)
                value = value_node.text if value_node is not None else ""
                if cell.attrib.get("t") == "s" and value:
                    value = shared[int(value)]
                values[col] = value
            if values:
                raw_rows.append(values)

    if not raw_rows:
        return []
    header = {col: _normalize_header(value) for col, value in raw_rows[0].items()}
    return [
        {header.get(col, col): value for col, value in row.items() if header.get(col, col)}
        for row in raw_rows[1:]
    ]


def _market_cap_file(config: RuntimeConfig) -> tuple[Path | None, list[str]]:
    market_cap_config = config.settings.get("universe", {}).get("market_cap", {})
    local_file = market_cap_config.get("local_file")
    if not local_file:
        return None, ["Market-cap universe local_file is not configured."]
    path = project_path(config, local_file)
    if path.exists():
        return path, []
    url = market_cap_config.get("remote_url", "")
    if not url:
        return None, [f"Market-cap universe file missing and no remote_url configured: {path}"]
    try:
        _download_file(url, path)
        return path, []
    except Exception as exc:
        return None, [f"Market-cap universe download failed from {url}: {exc}"]


def _load_market_cap_rows(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".csv":
        return [
            {(_normalize_header(k)): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace")))
        ]
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        return _xlsx_rows(path)
    return []


def _market_cap_lakh_from_row(row: dict[str, str]) -> float | None:
    explicit = _parse_float(
        row.get("market_cap_rs_lakhs")
        or row.get("market_cap_lakhs")
        or row.get("market_capitalisation_rs_lakhs")
    )
    if explicit is not None:
        return explicit
    for key, value in row.items():
        normalized_key = key.lower()
        if ("market_cap" in normalized_key or "market_capitalisation" in normalized_key) and "lakh" in normalized_key:
            parsed = _parse_float(value)
            if parsed is not None:
                return parsed
    return None


def load_market_cap_universe_with_warnings(
    config: RuntimeConfig,
    min_market_cap_cr: float,
    max_market_cap_cr: float,
) -> tuple[dict[str, Company], list[str]]:
    warnings: list[str] = []
    path, file_warnings = _market_cap_file(config)
    warnings.extend(file_warnings)
    if not path:
        return {}, warnings

    companies: list[Company] = []
    rows = _load_market_cap_rows(path)
    for row in rows:
        symbol = (row.get("symbol") or row.get("nse_symbol") or "").strip().upper()
        if not symbol:
            continue
        market_cap_lakh = _market_cap_lakh_from_row(row)
        market_cap_cr = _parse_float(row.get("market_cap_cr"))
        if market_cap_cr is None and market_cap_lakh is not None:
            market_cap_cr = market_cap_lakh / 100.0
        if market_cap_cr is None:
            continue
        if min_market_cap_cr <= market_cap_cr <= max_market_cap_cr:
            companies.append(
                Company(
                    symbol=symbol,
                    name=row.get("company_name") or symbol,
                    index=f"NSE market-cap {min_market_cap_cr:.0f}-{max_market_cap_cr:.0f} cr",
                    market_cap_cr=market_cap_cr,
                )
            )

    if not companies:
        warnings.append(
            f"Market-cap universe produced no companies for {min_market_cap_cr:.0f}-{max_market_cap_cr:.0f} cr from {path}."
        )
    else:
        warnings.append(
            f"Market-cap universe used {path.name}; {len(companies)} companies in "
            f"{min_market_cap_cr:.0f}-{max_market_cap_cr:.0f} cr."
        )
    return _merge_companies(companies), warnings


def load_universe_with_warnings(config: RuntimeConfig) -> tuple[dict[str, Company], list[str]]:
    companies: list[Company] = []
    warnings: list[str] = []

    midcap, mid_warnings = _load_index(config, "midcap150", "Nifty Midcap 150")
    smallcap, small_warnings = _load_index(config, "smallcap250", "Nifty Smallcap 250")
    warnings.extend(mid_warnings)
    warnings.extend(small_warnings)
    companies.extend(midcap)
    companies.extend(smallcap)

    if not companies:
        inferred = _infer_universe_from_local_sources(config)
        if inferred:
            warnings.append(
                "Universe inferred from local snapshots (manual_events/price_volume/fundamentals) "
                "because Midcap/Smallcap constituent lists were unavailable."
            )
            companies = inferred
        else:
            warnings.append(
                "Universe is empty (no constituent lists, seeds, or local snapshots). "
                "Report may contain no company-level signals."
            )

    return _merge_companies(companies), warnings


def load_universe(config: RuntimeConfig) -> dict[str, Company]:
    companies, _warnings = load_universe_with_warnings(config)
    return companies
