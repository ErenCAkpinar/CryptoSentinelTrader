"""
Exporter — turns the live bot's append-only log + state into track_record.json.

READ-ONLY. This does not import or touch the trading engine; it only parses
artifacts the bot already wrote (`paper_bb.log`, `state_paper.json`) plus the
risk constants in `config.py`. Everything it emits must be reconstructable from
those raw files so the dashboard stays verifiable against `journalctl`.

Usage (on the server, defaults point at the live bot):
    python -m track_record.exporter \
        --log /root/BreakoutBot/paper_bb.log \
        --state /root/BreakoutBot/state_paper.json \
        --config /root/BreakoutBot/config.py \
        --out /var/www/track-record/track_record.json

Run from a systemd timer every ~10 min. See track_record/README.md.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    BacktestResult,
    Benchmark,
    EquityPoint,
    Meta,
    RiskConfig,
    RiskEvent,
    RiskSummary,
    Trade,
    TrackRecord,
)

# ── Log line grammar ─────────────────────────────────────────────────────────
# Every event line the bot writes via its _log() looks like:
#   [2026-06-15 10:35:33 UTC]   <message>
_PREFIX = re.compile(r"^\s*\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]\s*(.*)$")

_NUM = r"([-+]?\d+(?:\.\d+)?)"

# Trades / scratches (message body, emoji included)
_RE_FULL_OPEN = re.compile(rf"📈 FULL OPEN\s+(\w+)\s+(LONG|SHORT)\s+@\s+{_NUM}\s+\|\s+notional=\$?{_NUM}\s+\|\s+bal=\$?{_NUM}")
_RE_CLOSE_FULL = re.compile(rf"(✅|❌) CLOSE FULL\s+(\w+)\s+\[(\w+)\]\s+(LONG|SHORT)\s+entry={_NUM}\s+exit={_NUM}\s+pnl=\$?{_NUM}\s+\|\s+bal=\$?{_NUM}")
_RE_MR_OPEN = re.compile(rf"🔁 MR OPEN\s+(\w+)\s+@\s+{_NUM}\s+\|\s+notional=\$?{_NUM}\s+\|\s+bal=\$?{_NUM}")
_RE_MR_CLOSE = re.compile(rf"(✅|❌) MR\s+(TP|SL|TMO|TIMEOUT)\s+(\w+)\s+entry={_NUM}\s+exit={_NUM}\s+pnl=\$?{_NUM}\s+\|\s+bal=\$?{_NUM}")

# Risk circuit-breaker events (the heroes of the dashboard)
_RE_THROTTLE_ON = re.compile(rf"🔻 THROTTLE on: peak DD\s+{_NUM}%")
_RE_THROTTLE_OFF = re.compile(rf"🔺 THROTTLE off: peak DD\s+{_NUM}%")
_RE_HARD_STOP = re.compile(rf"🛑 PEAK DD\s+{_NUM}% hit PEAK_DD_LIMIT")
_RE_DAILY_FREEZE = re.compile(r"Daily SL limit reached — freezing (\w+) entries")

# Context
_RE_REGIME = re.compile(r"🧭 Regime refresh — BULL:\s+(.*?)\s+\(others")
_RE_NEW_DAY = re.compile(rf"📅 New day\s+([\d-]+)\s+\|\s+starting balance\s+\$?{_NUM}")
# Dense per-bar balance. The bar number is thousands-separated once the bot passes
# bar 1,000 ("Bar #12,649"), so the digit class must accept commas — without it the
# equity curve silently degrades to event-only points and last_event_at stops being
# a liveness signal.
_RE_BAR_BAL = re.compile(rf"Bar #[\d,]+\s+.*?\|\s+\$?{_NUM}\s+\(")
_RE_ANY_BAL = re.compile(rf"bal=\$?{_NUM}")

# Exit-type label normalization → schema ExitType
_EXIT_MAP = {
    "TP1": "TP1", "TP2": "TP2", "TRAIL": "TRAIL", "SL": "SL",
}
_MR_EXIT_MAP = {"TP": "MR_TP", "SL": "MR_SL", "TMO": "MR_TIMEOUT", "TIMEOUT": "MR_TIMEOUT"}

# Fresh-start / epoch boundary: the bot logs this when it resumes on a brand-new
# state file (bar #0). Everything before the LAST such line belongs to a previous
# epoch (e.g. the pre-promote MAIN that blew up to -20%) and must NOT be counted —
# otherwise the dashboard reports stale history instead of the current bot.
_RE_EPOCH = re.compile(r"Resumed from state_paper\.json.*bar #0")


def _parse_ts(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _find_epoch_start(lines: list[str]) -> int:
    """Index of the LAST fresh-start ('bar #0') marker. We report only from here
    on, so a state reset/promote cleanly discards the previous epoch's history."""
    start = 0
    for i, ln in enumerate(lines):
        if _RE_EPOCH.search(ln):
            start = i
    return start


def _read_config_constants(config_path: Path) -> RiskConfig:
    """Read the bot's risk constants directly from config.py via regex (no import,
    so we never pull in the engine or its side effects)."""
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

    def num(name: str, default: float) -> float:
        m = re.search(rf"^{name}\s*=\s*([-+]?\d+(?:\.\d+)?)", text, re.MULTILINE)
        return float(m.group(1)) if m else default

    return RiskConfig(
        risk_per_trade_usd=num("RISK_PER_TRADE_USD", 10.0),
        peak_dd_limit_pct=num("PEAK_DD_LIMIT", -0.15),
        throttle_dd_pct=num("EQUITY_THROTTLE_DD", -0.07),
        daily_dd_limit_pct=num("DAILY_DD_LIMIT", -0.05),
        leverage=int(num("LEVERAGE", 3)),
    )


def _symbols_from_state(state: dict) -> list[str]:
    """Universe = the regime keys the bot persisted (most reliable source)."""
    regime = state.get("regime") or {}
    if regime:
        return sorted(regime.keys())
    return sorted((state.get("mr_states") or {}).keys())


def _downsample(points: list[EquityPoint], cap: int = 1500) -> list[EquityPoint]:
    if len(points) <= cap:
        return points
    step = len(points) / cap
    out = [points[int(i * step)] for i in range(cap)]
    if out and out[-1].ts != points[-1].ts:
        out.append(points[-1])  # always keep the latest point
    return out


def parse_log(log_path: Path, risk_cfg: RiskConfig, scope_to_epoch: bool = True) -> dict:
    """Single pass over the log → trades, risk events, equity series.

    By default only the CURRENT bot epoch is parsed (everything after the last
    fresh-start marker), so a promote/state-reset discards stale history. Pass
    scope_to_epoch=False for the full lifetime.
    """
    trades: list[Trade] = []
    risk_events: list[RiskEvent] = []
    balance_series: list[tuple[datetime, float]] = []

    open_full: dict[str, dict] = {}  # symbol → {ts, entry, notional, regime}
    open_mr: dict[str, dict] = {}
    bull_set: set[str] = set()

    if not log_path.exists():
        return {"trades": trades, "risk_events": risk_events, "balance_series": balance_series,
                "epoch_start": None}

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    epoch_idx = _find_epoch_start(lines) if scope_to_epoch else 0
    epoch_start: datetime | None = None
    if epoch_idx and (em := _PREFIX.match(lines[epoch_idx])):
        epoch_start = _parse_ts(em.group(1))

    for line in lines[epoch_idx:]:
            m = _PREFIX.match(line)
            if not m:
                continue
            ts = _parse_ts(m.group(1))
            body = m.group(2)

            # Dense equity sampling: prefer the per-bar balance, else any bal=
            bar = _RE_BAR_BAL.search(body)
            if bar:
                balance_series.append((ts, float(bar.group(1))))

            # Regime context (drives regime_at_entry)
            rg = _RE_REGIME.search(body)
            if rg:
                inside = rg.group(1)
                bull_set = set(re.findall(r"([A-Z0-9]+USDT)", inside))
                continue

            if (mm := _RE_FULL_OPEN.search(body)):
                sym, side, entry, notional, bal = mm.groups()
                open_full[sym] = {
                    "ts": ts, "entry": float(entry), "notional": float(notional),
                    "side": side, "regime": "BULL" if sym in bull_set else "NEUTRAL",
                }
                balance_series.append((ts, float(bal)))
                continue

            if (mm := _RE_CLOSE_FULL.search(body)):
                _emoji, sym, label, side, entry, exit_, pnl, bal = mm.groups()
                op = open_full.get(sym, {})
                exit_type = _EXIT_MAP.get(label.upper(), "TRAIL")
                trades.append(Trade(
                    ts_open=op.get("ts"), ts_close=ts, symbol=sym, side=side,
                    sleeve="MOMENTUM", regime_at_entry=op.get("regime"),
                    risk_usd=risk_cfg.risk_per_trade_usd, notional=op.get("notional"),
                    entry=float(entry), exit=float(exit_), exit_type=exit_type,
                    pnl=float(pnl), balance_after=float(bal),
                    audit_note=_audit_note(side, op.get("regime"), exit_type, op.get("notional"), risk_cfg.risk_per_trade_usd),
                ))
                balance_series.append((ts, float(bal)))
                if exit_type in ("TP2", "TRAIL", "SL"):
                    open_full.pop(sym, None)  # final close; TP1 keeps the runner open
                continue

            if (mm := _RE_MR_OPEN.search(body)):
                sym, entry, notional, bal = mm.groups()
                open_mr[sym] = {"ts": ts, "entry": float(entry), "notional": float(notional)}
                balance_series.append((ts, float(bal)))
                continue

            if (mm := _RE_MR_CLOSE.search(body)):
                _emoji, label, sym, entry, exit_, pnl, bal = mm.groups()
                op = open_mr.pop(sym, {})
                exit_type = _MR_EXIT_MAP.get(label.upper(), "MR_TP")
                trades.append(Trade(
                    ts_open=op.get("ts"), ts_close=ts, symbol=sym, side="LONG",
                    sleeve="MR", regime_at_entry="NEUTRAL",
                    risk_usd=None, notional=op.get("notional"),
                    entry=float(entry), exit=float(exit_), exit_type=exit_type,
                    pnl=float(pnl), balance_after=float(bal),
                    audit_note="Mean-reversion fade in NEUTRAL regime (range-bound); netting-guarded.",
                ))
                balance_series.append((ts, float(bal)))
                continue

            # ── Risk circuit-breaker events ──────────────────────────────────
            if (mm := _RE_THROTTLE_ON.search(body)):
                risk_events.append(RiskEvent(ts=ts, type="THROTTLE_ON", detail=body.strip(),
                                             dd_pct=float(mm.group(1)) / 100.0))
                continue
            if (mm := _RE_THROTTLE_OFF.search(body)):
                risk_events.append(RiskEvent(ts=ts, type="THROTTLE_OFF", detail=body.strip(),
                                             dd_pct=float(mm.group(1)) / 100.0))
                continue
            if (mm := _RE_HARD_STOP.search(body)):
                risk_events.append(RiskEvent(ts=ts, type="HARD_STOP", detail=body.strip(),
                                             dd_pct=float(mm.group(1)) / 100.0))
                continue
            if (mm := _RE_DAILY_FREEZE.search(body)):
                risk_events.append(RiskEvent(ts=ts, type="DAILY_FREEZE",
                                             detail=f"Daily SL limit reached — froze {mm.group(1)} for the day."))
                continue

            # Fallback: capture balance from any remaining bal= line
            if (mm := _RE_ANY_BAL.search(body)):
                balance_series.append((ts, float(mm.group(1))))

    return {"trades": trades, "risk_events": risk_events, "balance_series": balance_series,
            "epoch_start": epoch_start}


def _audit_note(side, regime, exit_type, notional, risk_usd) -> str:
    where = f"Regime {regime}" if regime else "Regime n/a"
    sized = f"risk-sized ${notional:,.0f} notional (SL≈${risk_usd:.0f})" if notional else "risk-sized"
    exits = {
        "TP1": "hit TP1 → booked half, stop moved to break-even",
        "TP2": "hit TP2 → final target",
        "TRAIL": "trailing stop locked the move",
        "SL": "stopped out at the fixed-risk stop",
    }
    return f"{where}; confirmed {side} breakout, {sized}; {exits.get(exit_type, exit_type)}."


def _build_equity_curve(balance_series: list[tuple[datetime, float]]) -> tuple[list[EquityPoint], float, float, float]:
    """Return (curve, peak_balance, current_dd_pct, max_dd_pct)."""
    series = sorted(balance_series, key=lambda x: x[0])
    curve: list[EquityPoint] = []
    peak = series[0][1] if series else 0.0
    max_dd = 0.0
    for ts, bal in series:
        peak = max(peak, bal)
        dd = (bal - peak) / peak if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
        curve.append(EquityPoint(ts=ts, balance=bal, dd_pct=round(dd, 6)))
    current_dd = curve[-1].dd_pct if curve else 0.0
    return _downsample(curve), peak, current_dd, max_dd


def _fold_positions(trades) -> list[tuple[str, float]]:
    """Fold exit legs into round-trip POSITIONS → [(final_exit_type, net_pnl)].

    A momentum position that reaches TP1 closes half there and the rest later
    (TP2 / TRAIL), so it emits TWO log records — and because the trailing stop
    already sits above entry once TP1 is hit, both are profitable by
    construction. Counting records therefore double-counts every winner: on the
    live record the identity TP1 == TP2 + TRAIL held exactly (13 == 2 + 11) and
    the reported win rate was inflated by ~14 points (57.7% vs a true 43.6%).

    Reporting positions instead is what makes the published numbers honest.
    Mirrors BreakoutBot's metrics.aggregate_positions().
    """
    positions: list[tuple[str, float]] = []
    pending: dict[str, float] = {}          # symbol -> pnl banked at TP1
    for t in sorted(trades, key=lambda x: x.ts_close):
        et = t.exit_type
        if t.sleeve == "MR" or et.startswith("MR_"):
            positions.append((et, t.pnl))
        elif et == "TP1":
            if t.symbol in pending:          # defensive: TP1 without a close
                positions.append(("TP1", pending.pop(t.symbol)))
            pending[t.symbol] = t.pnl
        else:                                # TP2 / TRAIL / SL / TIMEOUT
            banked = pending.pop(t.symbol, 0.0)
            positions.append((et, banked + t.pnl))
    return positions


def _risk_summary(trades, risk_events, peak, current_dd, max_dd) -> RiskSummary:
    positions = _fold_positions(trades)
    wins = [p for _, p in positions if p > 0]
    losses = [p for _, p in positions if p <= 0]
    n = len(positions)
    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = abs(sum(losses) / len(losses)) if losses else 0.0
    gross_w, gross_l = sum(wins), abs(sum(losses))
    return RiskSummary(
        peak_balance=round(peak, 2),
        current_dd_pct=round(current_dd, 4),
        max_dd_pct=round(max_dd, 4),
        throttle_active=_last_throttle_active(risk_events),
        hard_stopped=any(e.type == "HARD_STOP" for e in risk_events),
        total_trades=n,
        win_rate_pct=round(100 * len(wins) / n, 1) if n else None,
        avg_win_usd=round(avg_w, 2) if wins else None,
        avg_loss_usd=round(-avg_l, 2) if losses else None,
        payoff_ratio=round(avg_w / avg_l, 2) if avg_l > 0 else None,
        breakeven_wr_pct=round(100 * avg_l / (avg_w + avg_l), 1) if (avg_w + avg_l) > 0 else None,
        expectancy_usd=round(sum(p for _, p in positions) / n, 2) if n else None,
        profit_factor=round(gross_w / gross_l, 2) if gross_l > 0 else None,
        dd_circuit_event_count=sum(1 for e in risk_events if e.type in ("THROTTLE_ON", "HARD_STOP")),
    )


def _last_throttle_active(risk_events) -> bool:
    state = False
    for e in sorted(risk_events, key=lambda x: x.ts):
        if e.type == "THROTTLE_ON":
            state = True
        elif e.type == "THROTTLE_OFF":
            state = False
    return state


def _compute_benchmark(symbols: list[str], since: datetime) -> Benchmark | None:
    """Equal-weight HODL of the bot's own universe + BTC/USD, since epoch.
    Best-effort: any network/API failure returns None and must never break the
    export (the dashboard simply omits the card)."""
    import urllib.request

    def close_at(sym: str, start_ms: int | None) -> float:
        url = (f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}"
               f"&interval=1h&limit=1")
        if start_ms is not None:
            url += f"&startTime={start_ms}"
        else:
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            url += f"&startTime={now_ms - 3_600_000}"
        with urllib.request.urlopen(url, timeout=15) as resp:
            return float(json.load(resp)[0][4])

    try:
        t0 = int(since.timestamp() * 1000)
        changes = []
        for sym in symbols:
            p0, p1 = close_at(sym, t0), close_at(sym, None)
            changes.append((p1 / p0 - 1.0) * 100.0)
        btc = (close_at("BTCUSDT", None) / close_at("BTCUSDT", t0) - 1.0) * 100.0
        return Benchmark(
            since=since,
            own_universe_hodl_pct=round(sum(changes) / len(changes), 2) if changes else None,
            btc_usd_hodl_pct=round(btc, 2),
        )
    except Exception:
        return None


# Documented Faz 4c validation results (from config.py comments). Overridable via
# --backtest-json. These are the bot's own published backtest numbers, not live.
_DEFAULT_BACKTEST = [
    BacktestResult(period="90d chop (8 coins, Faz 4c)", profit_factor=3.29, win_rate_pct=None,
                   max_dd_pct=-3.25, note="8/8 coins profitable; +$55.51/mo."),
    BacktestResult(period="240d deep bear", profit_factor=None, win_rate_pct=None,
                   max_dd_pct=-9.66, note="Defended: -$16.42/mo, no hard stop hit."),
]


def build_track_record(log_path: Path, state_path: Path, config_path: Path,
                       backtest_json: Path | None,
                       with_benchmark: bool = False) -> TrackRecord:
    risk_cfg = _read_config_constants(config_path)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    parsed = parse_log(log_path, risk_cfg)
    trades = parsed["trades"]
    risk_events = parsed["risk_events"]
    curve, peak, current_dd, max_dd = _build_equity_curve(parsed["balance_series"])

    symbols = _symbols_from_state(state) or sorted({t.symbol for t in trades})
    start_balance = curve[0].balance if curve else float(state.get("balance", 1000.0))
    current_balance = float(state.get("balance", curve[-1].balance if curve else start_balance))

    backtest = _DEFAULT_BACKTEST
    if backtest_json and backtest_json.exists():
        raw = json.loads(backtest_json.read_text(encoding="utf-8"))
        backtest = [BacktestResult(**b) for b in (raw if isinstance(raw, list) else [raw])]

    meta = Meta(
        generated_at=datetime.now(timezone.utc),
        symbols=symbols,
        start_balance=round(start_balance, 2),
        current_balance=round(current_balance, 2),
        first_event_at=curve[0].ts if curve else None,
        last_event_at=curve[-1].ts if curve else None,
        risk_config=risk_cfg,
    )

    benchmark = None
    if with_benchmark and symbols:
        epoch = parsed.get("epoch_start") or (curve[0].ts if curve else None)
        if epoch is not None:
            benchmark = _compute_benchmark(symbols, epoch)

    return TrackRecord(
        meta=meta,
        risk_summary=_risk_summary(trades, risk_events, peak, current_dd, max_dd),
        equity_curve=curve,
        trades=trades,
        risk_events=risk_events,
        backtest=backtest,
        benchmark=benchmark,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Export the live bot's track record to JSON (read-only).")
    ap.add_argument("--log", type=Path, default=Path("/root/BreakoutBot/paper_bb.log"))
    ap.add_argument("--state", type=Path, default=Path("/root/BreakoutBot/state_paper.json"))
    ap.add_argument("--config", type=Path, default=Path("/root/BreakoutBot/config.py"))
    ap.add_argument("--backtest-json", type=Path, default=None,
                    help="Optional JSON from backtest_engine --json to override the default validation cards.")
    ap.add_argument("--benchmark", action="store_true",
                    help="Compute own-universe + BTC/USD HODL benchmark since epoch (needs network; "
                         "failures are swallowed and the field is simply omitted).")
    ap.add_argument("--out", type=Path, default=Path("track_record.json"))
    args = ap.parse_args()

    tr = build_track_record(args.log, args.state, args.config, args.backtest_json,
                            with_benchmark=args.benchmark)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(tr.to_json(), encoding="utf-8")

    rs = tr.risk_summary
    print(f"✅ Wrote {args.out} — {rs.total_trades} trades, "
          f"max DD {rs.max_dd_pct:.1%}, bal ${tr.meta.current_balance:.2f}, "
          f"{len(tr.risk_events)} risk events, {len(tr.equity_curve)} equity points")


if __name__ == "__main__":
    main()
