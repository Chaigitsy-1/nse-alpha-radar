# NSE Alpha Radar

Daily research assistant for Indian listed companies, focused on finding rare, high-quality opportunities in the INR 3,000 cr to INR 50,000 cr market-cap band and in the Nifty Midcap/Smallcap universe.

This is not a buy/sell recommender. It is an evidence-ranking system. The goal is to reduce noise, surface companies that deserve deeper work, and clearly separate:

- strict actionable ideas
- high-quality watchlist names
- momentum-only names
- turnaround/capex lifecycle candidates
- red-flag/risk cases

The tracker is intentionally allowed to say: **"No actionable idea today."**

## Current Strategy Philosophy

The system is optimized for quality over quantity.

It looks for companies where multiple forms of evidence line up:

- business evidence: capex, commissioning, utilization, order visibility, growth guidance, results
- financial evidence: revenue growth, PAT growth, margin expansion, cash flow, debt reduction, loss-to-profit turnarounds
- market confirmation: relative strength, trend momentum, price/volume confirmation
- catalyst evidence: results, orders, approvals, buybacks, QIPs, M&A, commercial production
- risk evidence: auditor exits, defaults, investigations, pledges, downgrades, penalties, litigation

The highest quality pattern is not a single keyword. It is a sequence such as:

```text
capex announced -> commissioning -> utilization/ramp-up -> revenue/profit follow-through -> management guidance -> market confirmation
```

This is why a company can appear on the watchlist for several days before it becomes actionable, and why many names never become actionable.

## Repository Layout

```text
indian-stock-tracker/
  config/
    settings.json         # universe, source, report, and delivery defaults
    signals.json          # signal categories, keywords, thresholds, noise controls
  data/
    cache/                # cached NSE bhavcopy and index files
    fundamentals.csv      # optional local fundamentals snapshot
    manual_events.csv     # optional manual events/themes
    price_volume.csv      # optional local price-volume snapshot
    market_cap_*.xlsx     # NSE official market-cap workbook
  reports/
    stock_report_*.md     # generated reports
    strategy_progress.md  # memory file with strategy checkpoints/backtests
  stock_tracker/
    main.py               # CLI entry point and delivery summary body
    universe.py           # index and market-cap universe loaders
    sources.py            # NSE/BSE/bhavcopy/index data loaders
    signals.py            # raw signal classification
    scoring.py            # dedup, noise filters, score aggregation
    lifecycle.py          # derived capex lifecycle signal
    report.py             # Markdown report and quality tier logic
    delivery.py           # email and Telegram delivery
    replay.py             # historical report generation helper
    backtest.py           # report parser and price validation/backtest
```

## Main Features

- Loads Nifty Midcap 150 + Nifty Smallcap 250, or an NSE official market-cap filtered universe.
- Pulls NSE corporate announcements, board meetings, corporate actions, market snapshots, index closes, and bhavcopy data.
- Classifies signals into short-term triggers, long-term tailwinds, management guidance, financial quality, turnaround, technical volume, trend momentum, sentiment, red flags, and capex lifecycle.
- Downloads/caches important NSE attachments and performs local filing validation before any model is needed.
- Optionally runs a second AI validation pass through Codex queue, Ollama, or OpenRouter.
- Uses bhavcopy history for 5d/20d/60d trend and relative strength percentiles.
- Adds a first-pass technical risk/reward layer using bhavcopy price structure.
- Builds a derived capex lifecycle signal by connecting current signals with historical generated reports.
- Writes a daily Markdown report.
- Cleans up old full reports and raw filing attachments so local storage does not balloon.
- Sends Telegram/email summaries.
- Supports Codex scheduled automation at 8 AM.
- Supports replay/backtest workflows for validation.

## Data Sources

The tracker uses free/public sources where possible.

### Primary online sources

- NSE announcements API
- NSE board meetings API
- NSE corporate actions API
- NSE market snapshot endpoint
- NSE bhavcopy archive
- NSE index close archive
- BSE RSS candidates
- NSE official market-cap workbook

### Local optional sources

These CSVs improve quality but are not mandatory:

- `data/fundamentals.csv`
- `data/price_volume.csv`
- `data/manual_events.csv`
- `data/midcap150.csv`
- `data/smallcap250.csv`

If local index constituent files are missing, the project attempts configured remote URLs or seed/fallback sources.

## Universe Modes

### 1. Index universe

Default mode:

```powershell
python -B -m stock_tracker.main --date today
```

This scans:

- Nifty Midcap 150
- Nifty Smallcap 250

### 2. Market-cap universe

Recommended current production mode:

```powershell
python -B -m stock_tracker.main --date today --universe marketcap --min-market-cap-cr 3000 --max-market-cap-cr 50000 --output-dir reports\marketcap_3000_50000_daily
```

This uses the NSE official all-companies market-cap workbook configured in `config/settings.json`.

Current default market-cap source:

```text
data/market_cap_nse_2025_12_31.xlsx
```

The workbook reports market capitalization in INR lakhs. The tracker converts it to INR crore:

```text
market_cap_cr = market_cap_lakhs / 100
```

## Signal Categories

Signal definitions live in `config/signals.json`.

### Turnaround

Looks for operational or financial revival language:

- turnaround
- restructuring
- deleveraging
- debt reduction
- EBITDA positive
- loss reduced
- margin expansion
- plant restart
- loss to profit
- profitability improved
- operating leverage

Base category weight: `1.35`

### Short-Term Trigger

Looks for events that may cause near-term price discovery:

- order win
- LOA
- contract
- board meeting
- results
- buyback
- bonus
- split
- QIP
- open offer
- merger/acquisition
- commissioning
- commercial production
- rating upgrade

Base category weight: `1.20`

Noise control is strict here. Calendar-only events such as only board meeting, record date, or dividend are down-weighted or skipped unless they are paired with higher-value words such as results, order win, commissioning, buyback, QIP, etc.

### Long-Term Tailwind

Looks for durable business or sector tailwinds:

- capex
- capacity expansion
- new plant/facility
- debottlenecking
- PLI
- Jal Jeevan Mission
- railway
- defence
- electronics manufacturing
- semiconductor
- renewable energy
- solar/wind/transmission
- EV
- data center
- housing
- China plus one
- domestic manufacturing
- growth engine
- addressable market
- sector tailwind

Base category weight: `1.25`

### Management Guidance

Looks for management commentary that can change future expectations:

- guidance
- outlook
- revenue visibility
- order book
- pipeline
- margin guidance
- growth guidance
- demand outlook
- operating leverage
- investor presentation
- concall
- analyst meet
- business update

Base category weight: `1.15`

### Financial Quality

Looks for improving financial quality:

- free cash flow
- ROCE/ROE
- debt free
- working capital improvement
- revenue growth
- profit growth
- PAT growth
- volume growth
- margin improvement
- asset turnover

Base category weight: `1.05`

### Filing Validation

The tracker has a local Python filing-validation layer.

It runs only on important NSE filings such as:

- results/outcome filings
- investor presentations
- press releases
- concalls/earnings call transcripts
- order/capex/commissioning/commercial production updates

What it does locally:

- downloads and caches the attachment under `data/filing_cache/`
- parses XML/XBRL directly with Python
- attempts dependency-free PDF text extraction when possible
- scans extracted text and NSE metadata for growth, margin, capex, order book, guidance, turnaround, and red-flag terms
- creates a `filing_validation` signal when it finds supporting evidence

### AI Filing Validation

The deterministic filing-validation layer is always the first line of defense. A second AI layer can be enabled when deeper judgement is needed.

Set one provider in `.env`:

```env
AI_PROVIDER=none
```

Supported values:

- `none`: default; Python-only deterministic validation
- `codex_queue`: writes shortlisted filings to `data/ai_validation_queue/YYYY-MM-DD/` for Codex to validate using the workspace model
- `ollama`: sends shortlisted snippets to a local Ollama model
- `openrouter`: sends shortlisted snippets to OpenRouter

Codex queue mode:

```env
AI_PROVIDER=codex_queue
AI_VALIDATION_MAX_FILINGS=8
```

The first run writes JSON packets for important filings. Codex can read those packets, write result JSON files into `data/ai_validation_results/YYYY-MM-DD/`, then rerun the report so validated signals are merged. This uses Codex itself, so no extra API key is required inside Codex.

Ollama mode:

```env
AI_PROVIDER=ollama
OLLAMA_MODEL=phi3
OLLAMA_URL=http://127.0.0.1:11434/api/generate
```

OpenRouter mode:

```env
AI_PROVIDER=openrouter
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=openai/gpt-4o-mini
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1/chat/completions
```

The AI prompt asks for strict JSON with verdict, materiality, sentiment, confidence, category, evidence, and manual checks. Only `PASS` or useful `PARTIAL` outputs become `ai_validation` signals. Negative or risky outputs become `red_flag` signals.

Design principle:

```text
Python/rules first -> AI only on shortlisted filings -> fallback to deterministic validation if AI fails
```

### Technical Volume

Looks for price/volume confirmation:

- price move >= 3%
- big price move >= 5%
- volume spike >= 2.5x
- delivery spike >= 2.0x

Confidence: `0.65`

Technical-volume signals are filtered out unless there is also `trend_momentum` for the same stock. This reduces one-day spike noise.

### Trend Momentum

Built from NSE bhavcopy closes.

Rules:

- 5 trading day return >= 3%
- 20 trading day return >= 10%
- 60 trading day return >= 20%
- 20d relative-strength percentile >= 65
- 60d relative-strength percentile >= 60

The signal requires multi-horizon confirmation:

```text
(20d and 60d strength) OR (60d and 5d strength)
```

and relative strength versus the scanned universe.

Overextension dampening:

- 20d return >= 45%, or
- 60d return >= 90%

When overextended:

- score multiplier becomes `0.55`
- confidence is capped at `0.58`

### Red Flag

Looks for potential governance, legal, credit, or operational risk:

- auditor resignation
- forensic audit
- fraud
- default
- promoter pledge
- qualified/adverse opinion
- income tax search
- ED/raid
- insolvency/NCLT
- downgrade
- litigation
- penalty
- shutdown/strike
- non-compliance

Base category weight: `-1.50`

Important limitation: generic words such as `SEBI` can appear in routine filings. These should be reviewed carefully before treating them as real red flags.

## Score And Confidence Calculations

Each signal has:

```text
signal_score
confidence
horizon
category
```

The contribution of a signal is:

```text
weighted_signal = signal_score * confidence
```

### Keyword-based source signals

For NSE/BSE/manual text signals:

```text
base_score = category_weight * min(3.0, 1.0 + matched_keyword_count * 0.25)
confidence = min(0.9, 0.45 + matched_keyword_count * 0.08)
```

So multiple relevant keywords increase both score and confidence, but both are capped.

### Fundamental signals

From `data/fundamentals.csv`:

- sales growth >= 15% adds `0.9`
- profit growth >= 20% adds `1.0`
- EBITDA margin expansion >= 150 bps adds `1.0`
- debt reduction >= 10% adds `0.9`
- previous profit < 0 and latest profit >= 0 adds `1.8`

Confidence: `0.72`

If a company moves from loss to profit, it becomes a `turnaround` signal. Otherwise it becomes `financial_quality`.

### Price/volume signals

From NSE market snapshots, bhavcopy-derived rows, or local `price_volume.csv`:

- price move >= 5% adds `1.4`
- price move >= 3% adds `0.8`
- volume spike >= 2.5x adds `1.1`
- delivery spike >= 2.0x adds `0.9`

Confidence: `0.65`

### Trend momentum signals

From bhavcopy 5d/20d/60d returns:

- 5d return condition adds `0.6`
- 20d return condition adds `1.0`
- 60d return condition adds `1.1`
- relative-strength percentiles are included in the label and used as gates

Confidence: `0.70`, unless overextended.

### Capex lifecycle signals

The derived lifecycle engine detects stages:

- capex/capacity
- commissioning
- utilization/ramp-up
- demand/order visibility
- financial follow-through
- management guidance
- market confirmation

It uses current signals and prior generated reports in the same report folder.

It only fires when:

- at least 2 lifecycle stages are present
- at least one core stage is present: capex, commissioning, or utilization
- at least one follow-through stage is present: demand, financials, guidance, or market confirmation
- the current day has lifecycle relevance

Score:

```text
base_score = 1.0 + min(3.2, stage_count * 0.45)
```

Additional boosts:

- financial follow-through: `+0.45`
- commissioning + utilization: `+0.35`

Confidence:

```text
confidence = min(0.84, 0.48 + stage_count * 0.07)
```

## Company Score Calculation

Signals are first deduplicated. If similar signals repeat, the strongest weighted signal is kept.

Then technical one-day spikes are filtered unless trend momentum exists for the same symbol.

Signals are aggregated by horizon:

```text
short signal       -> short_term += weighted
turnaround signal  -> turnaround += weighted; medium_term += weighted * 0.6
long signal        -> long_term += weighted; medium_term += weighted * 0.5
risk signal        -> risk += abs(weighted)
review signal      -> medium_term += weighted * 0.4
```

Final total:

```text
positive = short_term + medium_term + long_term + turnaround
total = positive - (risk * 1.4)
```

Event-only companies are penalized:

```text
if all signals are short_term_trigger:
    total *= 0.65
```

This prevents board-meeting/results-calendar noise from dominating the report.

## Risk/Reward Calculation

The tracker now adds a first-pass **technical risk/reward** review for each scored company.

This is not a full intrinsic valuation model. It does not know fair value, DCF, PE bands, or earnings upgrades yet. It uses recent NSE bhavcopy OHLC data to decide whether the current price offers a reasonable entry structure.

For each scored stock, the engine calculates:

- CMP from latest available bhavcopy close
- 20-trading-day support zone
- stop-loss/invalidation below support
- 60-trading-day high
- target based on 60d high or a minimum reward multiple
- downside percentage
- upside percentage
- reward:risk ratio
- entry verdict

Formula outline:

```text
support = max(20d_low, 20d_average_close * 0.92)
stop_loss = support * 0.97
risk_per_share = max(CMP - stop_loss, CMP * 0.02)
target = max(60d_high, CMP + risk_per_share * 1.8)
downside_pct = (CMP - stop_loss) / CMP * 100
upside_pct = (target - CMP) / CMP * 100
reward_risk = upside_pct / downside_pct
```

Verdicts:

- `Attractive R/R`: reward:risk >= 2.0 and downside <= 10%
- `Review entry`: reward:risk >= 1.5 and downside <= 12%
- `Neutral R/R`: needs better entry or stronger upside evidence
- `Wait pullback`: stock is stretched versus recent base
- `Poor R/R`: upside does not compensate downside
- `Avoid chase`: downside to invalidation is too wide

The strict `Actionable Today` gate now requires acceptable risk/reward too:

```text
reward_risk >= 1.2
downside_pct <= 15%
verdict is not Poor R/R or Avoid chase
```

This makes `Actionable Today` closer to a trade/investment review candidate, not just a research candidate.

## Quality Tiers

Quality tiers are computed in `stock_tracker/report.py`.

### A+

Lifecycle plus confirmation.

Typically means:

- capex lifecycle is present
- plus financial, guidance, or market confirmation
- confidence is strong enough

### A

Thesis plus confirmation.

Typically means:

- lifecycle or turnaround exists
- plus market confirmation, guidance, or financial proof

### B

Thesis signal, but needs stronger proof.

Usually a good research candidate but not enough for the strict actionable bucket.

### C

Momentum without business thesis, or mixed/incomplete evidence.

Useful for monitoring, but not a high-quality opportunity by itself.

### D

Event/calendar heavy.

Usually noisy.

### Avoid

Risk/red-flag dominated.

## Report Sections

Generated reports include:

### Actionable Today

Strictest section. Can be empty.

Requires:

- tier `A` or `A+`
- core business evidence: capex lifecycle, turnaround, or financial quality
- risk < `1.2`
- total score >= `3.0`
- strong lifecycle, turnaround confirmation, or financial confirmation

### High-Quality Watchlist

Interesting A+/A/B names that fail the final actionable gate.

This is where many serious candidates live before validation.

### 3-6 Month Event And Momentum

Short-term catalyst and momentum names.

Includes more C-tier names, so use it as a monitoring list, not a buy list.

### Deep Turnaround Watch

Names with turnaround signals.

### 1-3 Year Growth And Tailwinds

Long-term business and sector tailwind candidates.

### Red Flag Review

Names with risk signals.

## How To Run

Open PowerShell:

```powershell
cd C:\Users\Chaitanya\OneDrive\Documents\Playground\indian-stock-tracker
```

### Run today's index report

```powershell
python -B -m stock_tracker.main --date today
```

### Run today's market-cap report

```powershell
python -B -m stock_tracker.main --date today --universe marketcap --min-market-cap-cr 3000 --max-market-cap-cr 50000 --output-dir reports\marketcap_3000_50000_daily
```

### Run and send Telegram/email

```powershell
python -B -m stock_tracker.main --date today --universe marketcap --min-market-cap-cr 3000 --max-market-cap-cr 50000 --output-dir reports\marketcap_3000_50000_daily --send
```

`python -B` is used because OneDrive can sometimes create permission issues with `__pycache__` bytecode files.

## Delivery Setup

Create a `.env` file in the repo root.

### Telegram-only setup

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
EMAIL_TO=chaitusolasa1@gmail.com

TELEGRAM_BOT_TOKEN=your_full_botfather_token
TELEGRAM_CHAT_ID=your_chat_id

AI_PROVIDER=none
AI_VALIDATION_MAX_FILINGS=8
OLLAMA_MODEL=
OPENROUTER_API_KEY=
OPENROUTER_MODEL=openai/gpt-4o-mini
```

To get Telegram details:

1. In Telegram, open `@BotFather`.
2. Create/select your bot and copy the API token.
3. Open your bot and send `/start`.
4. Run:

```powershell
Invoke-RestMethod "https://api.telegram.org/botYOUR_FULL_TOKEN/getUpdates"
```

5. Use:

```text
result[0].message.chat.id
```

as `TELEGRAM_CHAT_ID`.

### Gmail setup

For Gmail SMTP:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email@gmail.com
SMTP_PASSWORD=your_16_character_gmail_app_password
EMAIL_TO=chaitusolasa1@gmail.com
```

Do not use your normal Gmail password. Use a Google App Password.

## Telegram Message Format

Telegram uses a mobile-friendly card layout instead of a wide table. The bot sends HTML-formatted text, so section headers, actionable stock names, and key fields such as `Action`, `R/R`, `Validation`, and `Found` appear in bold on mobile.

Example:

```text
NSE Alpha Radar
Date: 2026-05-10
Universe: NSE market-cap Rs 3,000-50,000 cr
Validation: AI merged 2 Codex result(s); top filings capped; local fallback active

Actionable today: 0
High-quality watchlist: 5

ACTIONABLE TODAY
----------------
No strict actionable setup today.

HIGH-QUALITY WATCHLIST
----------------------

------------------------------
1. NETWEB - Netweb Technologies India Limited

Grade: A+ (lifecycle plus confirmation)
Action: Research Candidate
Score: 4.32 | Risk: 0.00
R/R: Review entry; CMP 1234.00; stop 1120.00; target 1460.00; R/R 1.85x

Validation: Local filing fallback - validated filing: commercial production
Found: Filing text supports the catalyst; commencement of commercial production
Signal: trend_momentum...; capex_lifecycle...

Next: Validate filing, numbers, and management commentary.
```

## Scheduling

Codex automation has been configured to run daily at 8:00 AM from this workspace.

When `AI_PROVIDER=codex_queue`, the automation uses a two-pass flow:

```powershell
python -B -m stock_tracker.main --date today --universe marketcap --min-market-cap-cr 3000 --max-market-cap-cr 50000 --output-dir reports\marketcap_3000_50000_daily
```

Codex then validates any queue packets under `data/ai_validation_queue/YYYY-MM-DD/` and writes result JSON files under `data/ai_validation_results/YYYY-MM-DD/`.

Finally it reruns with delivery:

```powershell
python -B -m stock_tracker.main --date today --universe marketcap --min-market-cap-cr 3000 --max-market-cap-cr 50000 --output-dir reports\marketcap_3000_50000_daily --send
```

If `.env` has Telegram credentials, Telegram will send. If email SMTP is blank, email will be skipped while the report is still generated.

## Cleanup And Retention

The tracker automatically cleans generated artifacts after each run.

Configured in `config/settings.json`:

```json
"cleanup": {
  "enabled": true,
  "keep_full_reports_days": 7,
  "keep_filing_cache_days": 2,
  "keep_ai_queue_days": 2,
  "keep_ai_results_days": 30,
  "keep_bhavcopy_cache_days": 120
}
```

Default behavior:

- daily summary files are kept
- full Markdown reports older than 7 days are deleted from the active output folder
- downloaded filing attachments/PDFs older than 2 days are deleted
- AI queue packets older than 2 days are deleted
- AI result summaries older than 30 days are deleted
- bhavcopy/index cache files older than 120 days are deleted

This keeps the daily system useful without letting PDFs and replay reports consume disk space. For long-term memory, keep compact summaries such as `daily_summary_YYYY-MM-DD.md`, `combined_*.md`, and `reports/strategy_progress.md`.

## Replay And Historical Digests

Run a 15-day replay manually:

```powershell
$start=[datetime]'2026-04-26'
$end=[datetime]'2026-05-10'
for($d=$start; $d -le $end; $d=$d.AddDays(1)){
  $ds=$d.ToString('yyyy-MM-dd')
  python -B -m stock_tracker.main --date $ds --universe marketcap --min-market-cap-cr 3000 --max-market-cap-cr 50000 --output-dir reports\marketcap_3000_50000_15d
}
```

Prior generated examples:

- `reports/marketcap_3000_50000_15d/combined_2026-04-26_to_2026-05-10.md`
- `reports/marketcap_3000_50000_15d_telegram_test/telegram_digest_2026-04-26_to_2026-05-10.txt`

## Backtesting

Backtesting parses generated Markdown reports and validates forward returns using NSE bhavcopy closes.

Example:

```powershell
python -B -m stock_tracker.backtest --start 2025-11-10 --end 2026-05-10 --report_dir reports\replay_ruthless_strict_20251110_20260510 --out reports\backtest_detailed_ruthless_strict_20251110_20260510.csv
```

Backtest outputs include:

- trigger date
- stock return to latest
- fixed trading-day forward returns
- alpha versus Nifty Midcap 150
- alpha versus Nifty Smallcap 250
- quality tier
- signal categories

Important: backtests are only as good as the data and report logic at that time. Small samples, especially A/A+ strict actionable names, should not be over-trusted.

## Current Strategy Checkpoints

See:

```text
reports/strategy_progress.md
CHANGELOG.md
```

Key current findings:

- Capex lifecycle has been the strongest strategy addition so far.
- Strict actionable mode produces very few names, which matches the desired quality-first philosophy.
- Watchlist volume is still too broad on result-heavy days.
- Primary filing validation is the next major improvement.
- Current A+ labels are useful but not automatically enough; they still need source-file validation.

## Where GenAI Is Required

The core tracker does **not** require GenAI.

Plain Python handles:

- data fetching
- signal classification
- scoring
- report generation
- Telegram/email delivery
- replay/backtest workflows

Codex/GenAI is useful for:

- daily automation inside Codex
- interpreting reports
- reviewing queued filings and concalls
- adding new signal logic
- validating false positives
- improving signal weights
- summarizing large reports
- iterating the strategy based on backtest results

Future optional GenAI use cases:

- parse concall transcripts
- summarize management guidance
- detect guidance credibility changes
- compare stated guidance versus actual financial delivery
- identify sector tailwinds from government policy/news
- classify whether a filing is routine or truly material

The intended design is hybrid:

```text
Python = repeatable evidence engine
Codex/AI = analyst layer, validation, iteration, and deeper reading
```

Current AI options:

- Codex queue mode: `AI_PROVIDER=codex_queue`
- Ollama local mode: `AI_PROVIDER=ollama` plus `OLLAMA_MODEL`
- OpenRouter mode: `AI_PROVIDER=openrouter` plus `OPENROUTER_API_KEY`

All AI modes are optional. If no provider is configured or the provider fails, the report still runs with deterministic filing validation.

## Prerequisites

- Windows PowerShell
- Python 3.11+ recommended
- Internet access for NSE/BSE/Telegram
- Telegram bot token if using Telegram delivery
- Gmail App Password if using email delivery

No third-party Python packages are required for the current MVP. It uses the Python standard library.

Optional future packages are listed in `requirements.txt`.

## Known Limitations

- NSE/BSE endpoints can fail or rate-limit. The system reports source warnings.
- Weekend/holiday dates fall back to the latest available trading day for bhavcopy/index files.
- Some red-flag keywords can be noisy in routine compliance filings.
- Fundamentals are currently limited unless `data/fundamentals.csv` is maintained.
- The system does not yet parse full PDF concall transcripts or investor presentations deeply.
- PDF extraction is best-effort unless a stronger parser is added later.
- Telegram messages are capped around Telegram's message limits, so the full Markdown report remains the source of detail.
- Risk/reward is technical-price-structure based. It is not yet valuation-based.
- This is not financial advice and should not be used without manual filing review.

## Recommended Daily Workflow

1. Read Telegram summary.
2. If `Actionable Today` is empty, do not force a trade.
3. For A+/A watchlist names, open the full report.
4. Read the linked NSE filing.
5. Validate:
   - actual numbers
   - management guidance
   - capex stage
   - balance sheet risk
   - valuation/entry risk
   - whether price has already moved too much
6. Add manual notes or new event data if needed.
7. Let future reports confirm or invalidate the thesis.

## Quick Commands

Generate daily report:

```powershell
python -B -m stock_tracker.main --date today --universe marketcap --min-market-cap-cr 3000 --max-market-cap-cr 50000 --output-dir reports\marketcap_3000_50000_daily
```

Generate and send:

```powershell
python -B -m stock_tracker.main --date today --universe marketcap --min-market-cap-cr 3000 --max-market-cap-cr 50000 --output-dir reports\marketcap_3000_50000_daily --send
```

Open latest daily folder:

```powershell
Get-ChildItem reports\marketcap_3000_50000_daily
```

Check Telegram token:

```powershell
Invoke-RestMethod "https://api.telegram.org/botYOUR_FULL_TOKEN/getMe"
```

Check Telegram chat id:

```powershell
(Invoke-RestMethod "https://api.telegram.org/botYOUR_FULL_TOKEN/getUpdates").result[0].message.chat.id
```

## Final Reminder

This repo is a research filter. It is designed to help a human analyst avoid missing important opportunities while refusing to promote noisy ideas. The best output is not always a stock name. Sometimes the best output is:

```text
No actionable high-quality opportunity today.
```
