from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Company:
    symbol: str
    name: str = ""
    index: str = ""
    sector: str = ""
    industry: str = ""
    market_cap_cr: float | None = None


@dataclass(frozen=True)
class SourceItem:
    source: str
    title: str
    link: str = ""
    published: str = ""
    summary: str = ""
    symbol_hint: str = ""

    @property
    def text(self) -> str:
        return " ".join(part for part in [self.title, self.summary, self.symbol_hint] if part)


@dataclass
class Signal:
    symbol: str
    company_name: str
    category: str
    label: str
    score: float
    confidence: float
    evidence: str
    source: str
    link: str = ""
    horizon: str = "review"
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class RiskReward:
    symbol: str
    as_of: str
    cmp: float
    support: float
    stop_loss: float
    target: float
    downside_pct: float
    upside_pct: float
    reward_risk: float
    verdict: str
    note: str = ""


@dataclass
class CompanyScore:
    symbol: str
    company_name: str
    short_term: float = 0.0
    medium_term: float = 0.0
    long_term: float = 0.0
    turnaround: float = 0.0
    risk: float = 0.0
    total: float = 0.0
    signals: list[Signal] = field(default_factory=list)
    risk_reward: RiskReward | None = None
