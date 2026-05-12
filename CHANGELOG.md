# Changelog

All notable changes to the Indian Stock Tracker (signals, scoring, reports, and backtesting) are recorded here.

## Unreleased

### AI Validation Providers
- Added provider-based AI filing validation.
- `AI_PROVIDER=codex_queue` writes important filings to a local queue that Codex can validate without a separate API key.
- `AI_PROVIDER=ollama` validates through a local Ollama model.
- `AI_PROVIDER=openrouter` validates through OpenRouter using `OPENROUTER_API_KEY`.
- If AI is unavailable, the tracker still runs with deterministic Python filing validation.
- Added cleanup retention for AI validation queue/results so raw filing packets do not grow forever.

## 2026-05-10

### Filing Validation Engine
- Added a local Python filing-validation layer.
- Important NSE attachments are downloaded and cached under `data/filing_cache/`.
- XML/XBRL filings are parsed directly with Python.
- PDF extraction is attempted with a dependency-free best-effort parser.
- Validation signals can now promote evidence found inside filings, not just exchange headlines.
- Optional Ollama integration is supported through `OLLAMA_MODEL`; only shortlisted filing snippets are sent to the local model.

### Cleanup / Retention
- Added automatic cleanup after report generation.
- Keeps compact daily summaries while deleting older full reports from active output folders.
- Deletes old raw filing attachments from `data/filing_cache/`.
- Keeps bhavcopy/index cache for a configurable rolling window.

### Risk/Reward Layer
- Added a first-pass technical risk/reward model from NSE bhavcopy OHLC history.
- Each scored company can now include:
  - CMP
  - support
  - stop/invalidation
  - target
  - downside %
  - upside %
  - reward:risk ratio
  - entry verdict
- `Actionable Today` now requires acceptable technical risk/reward in addition to signal quality.
- Telegram mobile cards now include the risk/reward verdict.

### README Overhaul
- Replaced the lightweight README with a full operating manual covering:
  - repo structure
  - data sources
  - signal categories
  - confidence and score calculations
  - quality tiers
  - report sections
  - market-cap universe mode
  - Telegram/email setup
  - scheduling
  - replay/backtest usage
  - where GenAI/Codex is and is not required
  - current limitations and recommended daily workflow

### Market-Cap Universe Mode
- Added `--universe marketcap` to run the same strategy across NSE-listed companies filtered by official market-cap data.
- Added default support for NSE's December 31, 2025 all-companies average market-cap workbook.
- The requested band can be run with `--min-market-cap-cr 3000 --max-market-cap-cr 50000`.
- Generated report: `reports/marketcap_3000_50000/stock_report_2026-05-10.md`
  - Universe: 773 companies
  - Signals captured before scoring/dedup: 156
  - Companies with report signals: 125
  - Actionable today: 0
  - High-quality watchlist: 1 (`PAISALO`, B tier)
- Generated 15 daily reports for the same market-cap band:
  - Folder: `reports/marketcap_3000_50000_15d`
  - Date range: 2026-04-26 to 2026-05-10
  - Strict actionable names appeared only on 2 days:
    - 2026-04-28: `BRIGHOTEL`
    - 2026-04-30: `HFCL`

### Telegram Summary Upgrade
- Updated the delivery summary body to include the same decision fields that matter in the report:
  - `Actionable` count
  - high-quality watchlist count
  - `Grade`
  - `Action`
  - `Stock`
  - `Score`
  - compact top-signal reason
- This makes the Telegram/email message a priority dashboard instead of a plain top-score list.
- Replaced the wide table format with a mobile-readable card layout because Telegram wraps table columns poorly on phones.

### Capex Lifecycle Tracker
- Added a derived `capex_lifecycle` signal in `stock_tracker/lifecycle.py`.
- The lifecycle tracker connects current evidence with prior generated reports in the same report folder, avoiding a same-day-only blind spot.
- It detects stages:
  - capex/capacity announcement
  - commissioning / commercial production
  - utilization or ramp-up
  - demand/order visibility
  - financial follow-through
  - management guidance
  - market confirmation
- The signal only fires when a current signal connects to a multi-stage sequence with at least one core capex/commissioning/utilization stage and a follow-through/confirmation stage.

### Backtest Snapshot (capex lifecycle)
- Detailed CSV: `reports/backtest_detailed_lifecycle_20251110_20260510.csv`
- Replay folder: `reports/replay_lifecycle_20251110_20260510`
- Compared with quality-filtered baseline:
  - Picks: 270 -> 398
  - 21td mean alpha: +0.78% -> +1.16%; median alpha: -0.12% -> +0.35%
  - 63td mean alpha: +0.73% -> +1.89%; median alpha: -2.26% -> -0.23%
- `capex_lifecycle` subset:
  - 21td: mean alpha ≈ +2.43%, median ≈ +2.69%, win-rate ≈ 66.7% (n=18)
  - 63td: mean alpha ≈ +13.51%, median ≈ +13.61%, win-rate ≈ 88.9% (n=9)
- Interpretation: lifecycle sequencing is promising, especially medium-term, but sample size is still small. Next step is to make lifecycle evidence more precise by ingesting actual financial statements/transcripts instead of relying mainly on generated report history.

### Quality Tiers + Tier Backtest
- Added explicit `Quality Tier` to each report company block:
  - `A+`: lifecycle plus confirmation
  - `A`: thesis plus confirmation
  - `B`: thesis signal, needs stronger proof
  - `C`: momentum/mixed/incomplete evidence
  - `D`: event/calendar heavy
  - `Avoid`: risk-dominated
- Added daily `Quality Funnel` counts to the report.
- Added `quality_tier` to backtest CSV output.
- Detailed CSV: `reports/backtest_detailed_tiered_20251110_20260510.csv`
- Tier readout:
  - Overall Top Overall aggregate: 21td mean alpha ≈ +1.16%, median ≈ +0.35%; 63td mean alpha ≈ +1.89%, median ≈ -0.23%.
  - `B` tier had the best usable fixed-horizon sample: 21td mean alpha ≈ +1.87%, median ≈ +1.67% (n=51); 63td mean alpha ≈ +3.73%, median ≈ +0.40% (n=33).
  - `C` tier lagged: 21td mean alpha ≈ -0.24%, median ≈ -0.63% (n=42); 63td mean alpha ≈ -3.05%, median ≈ -3.08% (n=15).
  - `A+` fixed-horizon sample is too small because many A+ signals are recent; to-date alpha is weak, so the A+ rule is not yet reliable.
- Interpretation: explicit tiering is useful, but the tier logic needs calibration. Current evidence says `B` is more reliable than `C`, while `A+` needs better primary-evidence validation and more mature outcomes.

### Ruthless Mode
- Reworked report sections:
  - `Actionable Today`: strict A/A+ only, requiring core business evidence plus strong lifecycle/turnaround/financial confirmation.
  - `High-Quality Watchlist`: A/A+/B names that are interesting but fail the final actionable gate.
  - Momentum and event noise remain visible in separate sections but are not promoted.
- Backtest now evaluates `Actionable Today` instead of the old broader `Top Overall Opportunities`.
- Detailed CSV: `reports/backtest_detailed_ruthless_strict_20251110_20260510.csv`
- Replay folder: `reports/replay_ruthless_strict_20251110_20260510`
- Signal volume:
  - 52 report dates
  - 4 actionable signals total
  - 48 zero-actionable days
  - average ≈ 0.08 actionable ideas/day
  - high-quality watchlist average ≈ 8.33 names/day
- Fixed-horizon results:
  - 21td: n=1, alpha vs Midcap ≈ +4.64%
  - 63td: n=1, alpha vs Midcap ≈ +15.38%
- Interpretation: ruthless mode now matches the desired philosophy (rare ideas, no forced daily picks). Performance looks promising but is statistically too small to trust. The watchlist remains broad and needs further ranking/validation.

### Strategy Iteration (Relative Strength + Quality Top Overall)
- Added cross-sectional relative-strength percentiles to bhavcopy trend rows (`ret_20d_percentile`, `ret_60d_percentile`).
- `trend_momentum` now requires multi-horizon price strength plus relative-strength confirmation.
- Added overextension dampening for very stretched 20d/60d moves.
- Changed `Top Overall Opportunities` to exclude momentum-only names; pure trend/price-volume names still appear in the 3-6 month momentum section, but Top Overall now requires thesis/context signals such as turnaround, financial quality, long-term tailwind, management guidance, or meaningful non-momentum events.
- Expanded capex/utilization/growth keyword coverage: capacity additions, debottlenecking, ramp-up, utilization improvement, operating leverage, revenue/profit/volume growth, growth engines, addressable market, and sector tailwinds.

### Latest Backtest Snapshot (quality-filtered)
- Detailed CSV: `reports/backtest_detailed_quality_20251110_20260510.csv`
- Replay folder: `reports/replay_quality_20251110_20260510`
- Window: 2025-11-10 to 2026-05-10 (weekly + last 30 days daily reports)
  - Picks reduced: 510 -> 270
  - 21 trading days: mean alpha vs Midcap ≈ +0.78% (n=98), median ≈ -0.12%
  - 63 trading days: mean alpha vs Midcap ≈ +0.73% (n=54), median ≈ -2.26%
- Interpretation: mean alpha improved at both 21td and 63td, especially 63td (-0.82% -> +0.73%), but median/win-rate remain weak. The system is better as a quality-filtered research funnel, not yet a standalone ranking model.

### Reporting (One-Stop Actionable)
- Added per-company `Action`, `Confidence (overall)`, `Thesis`, and `Next steps` checklist.
- Added `Market Regime` section using NSE `ind_close_all_DDMMYYYY.csv` with fallback to last trading day.

### Backtesting (More Correct)
- Added bhavcopy-based historical price moves for backtests to avoid real-time snapshot leakage.
- Added fixed trading-day horizon metrics to backtest CSV (21/63/126 trading days) with benchmark alpha vs Nifty Midcap 150.
- Added `replay` runner to regenerate historical reports into isolated folders for backtests.

### Noise Reduction
- Down-weighted and filtered calendar-only triggers (`board meeting`, `record date`, `dividend`) unless paired with higher-value keywords.
- Added signal de-duplication (keeps strongest of near-duplicates).
- Added additional penalty for event-only companies in totals to reduce calendar spam in Top Overall.

### New Signals
- Added bhavcopy-derived multi-horizon trend signal `trend_momentum` (5d/20d/60d) with multi-horizon confirmation gate.

### Latest Backtest Snapshot (bhavcopy-corrected)
- Detailed CSV: `reports/backtest_detailed_v3_20251110_20260510.csv`
- Window: 2025-11-10 to 2026-05-10 (weekly + last 30 days daily reports)
  - 21 trading days: mean alpha vs Midcap ≈ +0.61% (n=209)
  - 63 trading days: mean alpha vs Midcap ≈ -0.82% (n=126)
