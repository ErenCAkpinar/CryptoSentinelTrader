"""
Track-record data contract — the single bridge between exporter, web and Telegram.

This module defines `TrackRecord`, the schema of `track_record.json`. The exporter
parses the live bot's append-only log + state into this shape; the static web page
and the Telegram broadcaster only ever read it. Nothing here computes trading
signals — it is pure reporting over data the bot already produced.

Design rule: the dashboard's value is *verifiability*. Every field below must be
reconstructable from the raw `paper_bb.log` / `state_paper.json` so a reader can
check it against `journalctl -u breakoutbot`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

Side = Literal["LONG", "SHORT"]
Regime = Literal["BULL", "NEUTRAL", "BEAR"]
ExitType = Literal["TP1", "TP2", "TRAIL", "SL", "MR_TP", "MR_SL", "MR_TIMEOUT", "CONF_FAIL"]
RiskEventType = Literal["THROTTLE_ON", "THROTTLE_OFF", "HARD_STOP", "DAILY_FREEZE"]


class RiskConfig(BaseModel):
    """The live bot's risk parameters, read straight from its config.py so the
    dashboard can never drift from what the bot actually enforces."""

    risk_per_trade_usd: float = Field(..., description="Fixed $ risked per full trade (every SL costs ~this).")
    peak_dd_limit_pct: float = Field(..., description="Hard-stop drawdown from equity peak, e.g. -0.15.")
    throttle_dd_pct: float = Field(..., description="Drawdown at which sizing halves, e.g. -0.07.")
    daily_dd_limit_pct: float = Field(..., description="Intraday drawdown that freezes new entries.")
    leverage: int = Field(..., description="Leverage cap on a full position.")


class Meta(BaseModel):
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    mode: Literal["TESTNET", "PAPER", "LIVE"] = "TESTNET"
    bot_name: str = "BreakoutBot"
    strategy: str = "Faz 4c — regime-aware breakout + mean-reversion"
    symbols: list[str] = Field(default_factory=list)
    start_balance: float
    current_balance: float
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    risk_config: RiskConfig


class EquityPoint(BaseModel):
    ts: datetime
    balance: float
    dd_pct: float = Field(..., description="Drawdown from running peak at this point, e.g. -0.034.")


class Trade(BaseModel):
    ts_open: datetime | None = None
    ts_close: datetime
    symbol: str
    side: Side = "LONG"
    sleeve: Literal["MOMENTUM", "MR"] = "MOMENTUM"
    regime_at_entry: Regime | None = None
    risk_usd: float | None = Field(None, description="Intended $ risk for this trade (the discipline number).")
    notional: float | None = Field(None, description="Risk-sized position notional, NOT a flat amount.")
    entry: float | None = None
    exit: float
    exit_type: ExitType
    pnl: float
    balance_after: float
    audit_note: str = Field("", description="Plain-language 'why it entered/exited' for the transparency log.")


class RiskEvent(BaseModel):
    """A circuit-breaker firing. These are the hero of the dashboard: proof the
    guardrails actually trip, rather than a promise that they would."""

    ts: datetime
    type: RiskEventType
    detail: str
    balance: float | None = None
    dd_pct: float | None = None


class BacktestResult(BaseModel):
    period: str = Field(..., description="e.g. '90d chop' or '240d deep bear'.")
    profit_factor: float | None = None
    win_rate_pct: float | None = None
    max_dd_pct: float | None = None
    note: str = ""


class RiskSummary(BaseModel):
    peak_balance: float
    current_dd_pct: float
    max_dd_pct: float = Field(..., description="Worst drawdown ever reached — the headline 'doesn't blow up' number.")
    throttle_active: bool = False
    hard_stopped: bool = False
    total_trades: int = 0
    win_rate_pct: float | None = None
    avg_win_usd: float | None = None
    avg_loss_usd: float | None = None
    dd_circuit_event_count: int = 0


class TrackRecord(BaseModel):
    """Top-level document serialized to track_record.json."""

    meta: Meta
    risk_summary: RiskSummary
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    trades: list[Trade] = Field(default_factory=list)
    risk_events: list[RiskEvent] = Field(default_factory=list)
    backtest: list[BacktestResult] = Field(default_factory=list)

    def to_json(self, **kwargs) -> str:
        """Serialize to the on-disk JSON the web page and broadcaster consume."""
        kwargs.setdefault("indent", 2)
        return self.model_dump_json(**kwargs)
