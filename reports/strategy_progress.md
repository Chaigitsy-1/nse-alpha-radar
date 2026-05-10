# Strategy Progress

This file tracks how the strategy and report generator improve over time, with backtest checkpoints.

## 2026-05-10

### Latest checkpoint: market-cap universe expansion
- Added a market-cap universe mode so the strategy can run beyond just Nifty Midcap 150 + Nifty Smallcap 250.
- Source: NSE official all-companies average market-cap workbook for December 31, 2025.
- Requested run band: **Rs 3,000 cr to Rs 50,000 cr**.
- Generated report: `reports/marketcap_3000_50000/stock_report_2026-05-10.md`
- Run result:
  - Universe companies: **773**
  - Companies with report signals: **125**
  - Actionable today: **0**
  - High-quality watchlist: **1** (`PAISALO`, B tier)
- Readout: the strict filter is doing what we wanted: no forced actionable picks. The only watchlist name is mostly a results + relative-strength setup, so it needs filing/result validation before being treated as a real opportunity.

### 15-day market-cap replay
- Folder: `reports/marketcap_3000_50000_15d`
- Dates: **2026-04-26 to 2026-05-10**
- Universe: **773** companies per day, filtered by NSE official market-cap workbook.
- Strict actionable output:
  - 2026-04-28: **Brigade Hotel Ventures Limited (BRIGHOTEL)**
  - 2026-04-30: **HFCL Limited (HFCL)**
  - All other days: **0 actionable**
- Watchlist volume varied from **0 to 24** names/day. This is still broad on heavy result/announcement days and should be reduced further with primary-result validation.

### Latest checkpoint: capex lifecycle tracker
- Added derived `capex_lifecycle` signals that connect current evidence with prior generated reports.
- Stages tracked:
  - capex/capacity announcement
  - commissioning / commercial production
  - utilization or ramp-up
  - demand/order visibility
  - financial follow-through
  - management guidance
  - market confirmation

### Backtest checkpoint after lifecycle tracker
- CSV: `reports/backtest_detailed_lifecycle_20251110_20260510.csv`
- Replay folder: `reports/replay_lifecycle_20251110_20260510`
- Compared with previous quality-filtered baseline:
  - Picks: **270 -> 398**
  - 21td mean alpha: **+0.78% -> +1.16%**
  - 21td median alpha: **-0.12% -> +0.35%**
  - 63td mean alpha: **+0.73% -> +1.89%**
  - 63td median alpha: **-2.26% -> -0.23%**
- `capex_lifecycle` subset:
  - 21td: mean alpha **+2.43%**, median **+2.69%**, win-rate **66.7%** (n=18)
  - 63td: mean alpha **+13.51%**, median **+13.61%**, win-rate **88.9%** (n=9)

### Readout
- This is the strongest strategy addition so far.
- Caveat: lifecycle-specific sample size is small, so treat it as promising but not proven.
- Next improvement: ingest actual quarterly financials/transcripts to verify the lifecycle sequence from primary evidence instead of relying mainly on report-history-derived signals.

### Latest checkpoint: quality tiers
- Added report-level `Quality Tier` and `Quality Funnel`.
- Added `quality_tier` to backtest rows.
- CSV: `reports/backtest_detailed_tiered_20251110_20260510.csv`

### Tier backtest readout
- Aggregate Top Overall:
  - 21td mean alpha **+1.16%**, median **+0.35%**, win-rate **50.9%** (n=110)
  - 63td mean alpha **+1.89%**, median **-0.23%**, win-rate **48.3%** (n=60)
- `B` tier:
  - 21td mean alpha **+1.87%**, median **+1.67%**, win-rate **52.9%** (n=51)
  - 63td mean alpha **+3.73%**, median **+0.40%**, win-rate **54.5%** (n=33)
- `C` tier:
  - 21td mean alpha **-0.24%**, median **-0.63%**, win-rate **47.6%** (n=42)
  - 63td mean alpha **-3.05%**, median **-3.08%**, win-rate **33.3%** (n=15)
- `A+` tier:
  - Fixed-horizon sample is too small/mature to trust yet (21td n=3, 63td n=1).
  - To-date alpha is weak, so A+ should be treated as “needs urgent validation,” not automatically best.

### Tier lesson
- The quality tier is useful, but current labels are not calibrated perfectly.
- `B` is currently the most statistically useful tier.
- `C` underperforms and should probably be excluded from morning “priority” unless user explicitly wants broad monitoring.
- A+ should require stronger primary evidence before being treated as truly high quality.

### Latest checkpoint: ruthless mode
- Reworked report output so `Actionable Today` is allowed to be empty.
- Strict actionable gate requires:
  - A/A+ tier
  - core business evidence (`capex_lifecycle`, `turnaround`, or `financial_quality`)
  - strong lifecycle/turnaround/financial confirmation
  - low risk
  - total score >= 3.0
- CSV: `reports/backtest_detailed_ruthless_strict_20251110_20260510.csv`
- Replay folder: `reports/replay_ruthless_strict_20251110_20260510`

### Ruthless backtest readout
- 52 report dates
- 4 actionable signals total
- 48 dates with no actionable signal
- Average actionable ideas/day: **0.08**
- Average high-quality watchlist names/day: **8.33**
- Mature fixed-horizon sample:
  - 21td: n=1, alpha **+4.64%**
  - 63td: n=1, alpha **+15.38%**

### Ruthless lesson
- This matches the desired user philosophy: no forced ideas, quality over quantity.
- But the mature sample is tiny. This is a filter design checkpoint, not proof of predictive power.
- Next work should reduce watchlist size and add primary-evidence validation so strong ideas are not missed while keeping the actionable section rare.

### Latest checkpoint: quality-filtered Top Overall
- Implemented relative-strength trend controls and removed momentum-only names from `Top Overall Opportunities`.
- Added capex/utilization/growth lifecycle keywords so the report is more likely to capture:
  - capacity expansion / new capacity / debottlenecking
  - utilization ramp-up
  - operating leverage
  - revenue, volume, and PAT growth
  - sector/policy tailwinds and growth engines

### Backtest checkpoint after latest iteration
- CSV: `reports/backtest_detailed_quality_20251110_20260510.csv`
- Replay folder: `reports/replay_quality_20251110_20260510`
- Compared with previous `v3`:
  - Picks: **510 -> 270**
  - 21td mean alpha: **+0.61% -> +0.78%**
  - 21td median alpha: **-0.17% -> -0.12%**
  - 63td mean alpha: **-0.82% -> +0.73%**
  - 63td median alpha: **-2.02% -> -2.26%**

### Readout
- Good: fewer picks, better mean alpha, and 63td mean alpha is now positive.
- Still weak: median alpha and win-rate remain below what we want, so ranking quality is not yet reliable.
- Lesson: pure momentum is useful for a short-term watchlist, but `Top Overall` should prefer thesis-backed names.

### Next improvements
- Add actual financial statement ingestion instead of relying on local `fundamentals.csv`.
- Detect capex lifecycle as a connected sequence: capex announcement -> commissioning -> utilization ramp -> revenue/profit jump -> guidance upgrade.
- Add downside control: recent drawdown, volatility, and “vertical move” filters.
- Split backtests by report bucket, not just `Top Overall`, so short-term momentum and long-term thesis are judged separately.

### What changed
- One-stop actionable report output (Action / Confidence / Thesis / Next steps).
- Market regime section from NSE index close file (`ind_close_all`), with fallback to last trading day for weekends/holidays.
- Backtest correctness improvements:
  - Prevented data leakage by avoiding real-time index snapshot for historical dates.
  - Historical price moves use bhavcopy-based deltas for each report date.
  - Fixed-horizon (trading-day) returns + benchmark alpha added to CSV.
- Added multi-horizon `trend_momentum` signal (5d/20d/60d) with confirmation gate.

### Backtest checkpoints
- CSV (6-month replay): `reports/backtest_detailed_v3_20251110_20260510.csv`
  - 21 trading days: mean alpha vs Midcap ≈ **+0.61%** (n=209)
  - 63 trading days: mean alpha vs Midcap ≈ **-0.82%** (n=126)

### Notes / next improvements
- 21td looks improved; 63td still negative. Next focus: reduce mean-reversion traps and improve medium-term holding quality.
- Add “relative strength vs midcap” filter using bhavcopy history (rank/percentile) to avoid broad-market beta picks.
- Add downside control (drawdown proxy / volatility proxy) to cut left-tail events.
