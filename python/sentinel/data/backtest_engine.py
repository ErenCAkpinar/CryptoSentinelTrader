"""
CryptoSentinelTrader — Backtester
Fetches historical 1-minute OHLCV data from Binance Futures, computes all
technical indicators, runs the MathEngine decision logic, and simulates
paper trades with the same risk management as the live executor.

Usage:
    python -m backtest.backtester --symbol BTCUSDT --days 30
    python -m backtest.backtester --symbol ETHUSDT --days 7 --balance 5000
"""

from __future__ import annotations

import argparse
import json
import math
import ssl
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# macOS ships with unverified certs for Python — create a permissive context
# only for public read-only market data (no credentials involved).
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_engine.decision_engine import MathEngine

BINANCE_FUTURES = "https://fapi.binance.com"
KLINE_LIMIT = 1500          # Max candles per Binance request
FEE_RATE = 0.0005           # 0.05% taker fee per side
MAINTENANCE_BUFFER = 0.05   # 5% maintenance margin buffer for liquidation


# ── Indicator helpers ─────────────────────────────────────────────────────────

def _ema_series(values: list[float], period: int) -> list[Optional[float]]:
    """Full EMA series. Matches Rust calculator.rs formula."""
    n = len(values)
    if n < period:
        return [None] * n
    result: list[Optional[float]] = [None] * (period - 1)
    ema = sum(values[:period]) / period
    result.append(ema)
    k = 2.0 / (period + 1)
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
        result.append(ema)
    return result


def _rsi_at(closes: list[float], i: int, period: int = 14) -> float:
    """Simple RSI at index i. Matches Rust processor.rs formula."""
    if i < period:
        return 50.0
    start = i - period
    gains = losses = 0.0
    for j in range(start + 1, i + 1):
        change = closes[j] - closes[j - 1]
        if change > 0:
            gains += change
        else:
            losses += abs(change)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _atr_at(highs: list[float], lows: list[float], closes: list[float],
            i: int, period: int = 14) -> float:
    """ATR at index i."""
    if i < period:
        return 0.0
    trs = []
    for j in range(i - period + 1, i + 1):
        if j == 0:
            trs.append(highs[j] - lows[j])
        else:
            trs.append(max(
                highs[j] - lows[j],
                abs(highs[j] - closes[j - 1]),
                abs(lows[j] - closes[j - 1]),
            ))
    return sum(trs) / period


def _bb_position_pct(closes: list[float], i: int, period: int = 20,
                     mult: float = 2.0) -> tuple[float, float, float, float]:
    """Returns (upper, middle, lower, position_pct) at index i."""
    if i < period - 1:
        c = closes[i]
        return c, c, c, 50.0
    window = closes[i - period + 1: i + 1]
    sma = sum(window) / period
    variance = sum((x - sma) ** 2 for x in window) / period
    std = math.sqrt(variance)
    upper = sma + mult * std
    lower = sma - mult * std
    band_width = upper - lower
    pos = (closes[i] - lower) / band_width * 100 if band_width > 1e-10 else 50.0
    return upper, sma, lower, max(0.0, min(100.0, pos))


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Fetch OHLCV klines from Binance Futures (paginated, no auth needed)."""
    all_klines: list = []
    cursor = start_ms

    while cursor < end_ms:
        url = (
            f"{BINANCE_FUTURES}/fapi/v1/klines"
            f"?symbol={symbol}&interval={interval}"
            f"&startTime={cursor}&endTime={end_ms}&limit={KLINE_LIMIT}"
        )
        try:
            with urllib.request.urlopen(url, timeout=30, context=_SSL_CTX) as resp:
                batch = json.loads(resp.read())
        except Exception as e:
            print(f"  [fetch] Error: {e} — retrying in 5s")
            time.sleep(5)
            continue

        if not batch:
            break

        all_klines.extend(batch)
        print(f"  [fetch] {len(all_klines):,} candles fetched…", end="\r")

        if len(batch) < KLINE_LIMIT:
            break

        # Next page: +1 interval after the last candle's open time
        ms_per_interval = {"1m": 60_000, "5m": 300_000, "15m": 900_000}
        cursor = batch[-1][0] + ms_per_interval.get(interval, 60_000)
        time.sleep(0.15)   # polite rate limit

    print()
    return all_klines


# ── Trade simulation ──────────────────────────────────────────────────────────

@dataclass
class BacktestPosition:
    side: str
    entry_price: float
    entry_idx: int
    notional: float
    leverage: float
    stop_loss: float
    take_profit: float
    liquidation_price: float
    open_fee: float


@dataclass
class ClosedTrade:
    side: str
    entry_price: float
    exit_price: float
    pnl_usd: float
    pnl_pct: float
    notional: float
    fees_usd: float
    reason: str
    entry_idx: int
    exit_idx: int


# ── Main backtester ───────────────────────────────────────────────────────────

class Backtester:
    def __init__(
        self,
        symbol: str = "BTCUSDT",
        days: int = 30,
        interval: str = "1m",
        starting_balance: float = 1000.0,
        risk_per_trade: float = 0.03,
        max_leverage: int = 5,
        sl_atr_mult: float = 2.0,
        tp_atr_mult: float = 4.0,
        daily_loss_limit: float = 0.10,
    ):
        self.symbol = symbol
        self.days = days
        self.interval = interval
        self.balance = starting_balance
        self.starting_balance = starting_balance
        self.risk_per_trade = risk_per_trade
        self.max_leverage = max_leverage
        self.sl_atr_mult = sl_atr_mult
        self.tp_atr_mult = tp_atr_mult
        self.daily_loss_limit = daily_loss_limit

        self.math_engine = MathEngine()
        self.position: Optional[BacktestPosition] = None
        self.trades: list[ClosedTrade] = []
        self.balance_curve: list[float] = []

    def run(self) -> dict:
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = end_ms - self.days * 24 * 3600 * 1000

        print(f"\n{'═'*58}")
        print(f"  BACKTEST — {self.symbol} | {self.days}d | {self.interval} candles")
        print(f"{'═'*58}")
        print(f"  Fetching data from Binance Futures…")

        raw = fetch_klines(self.symbol, self.interval, start_ms, end_ms)
        if len(raw) < 60:
            print("  Not enough data. Exiting.")
            return {}

        # Parse OHLCV arrays
        opens  = [float(k[1]) for k in raw]
        highs  = [float(k[2]) for k in raw]
        lows   = [float(k[3]) for k in raw]
        closes = [float(k[4]) for k in raw]
        volumes = [float(k[5]) for k in raw]
        times  = [int(k[0]) for k in raw]

        n = len(closes)
        print(f"  {n:,} candles | {datetime.fromtimestamp(times[0]/1000, tz=timezone.utc).date()} "
              f"→ {datetime.fromtimestamp(times[-1]/1000, tz=timezone.utc).date()}")

        # Pre-compute EMA series (vectorised)
        ema9  = _ema_series(closes, 9)
        ema21 = _ema_series(closes, 21)
        ema50 = _ema_series(closes, 50)
        ema12 = _ema_series(closes, 12)
        ema26 = _ema_series(closes, 26)

        # MACD series
        macd_line: list[Optional[float]] = [
            (ema12[i] - ema26[i]) if (ema12[i] is not None and ema26[i] is not None) else None
            for i in range(n)
        ]
        # Signal line = EMA(9) of MACD line
        macd_valid_start = next((i for i, v in enumerate(macd_line) if v is not None), n)
        macd_values_for_signal = [v for v in macd_line if v is not None]
        ema9_macd = _ema_series(macd_values_for_signal, 9)
        signal_line: list[Optional[float]] = [None] * n
        si = 0
        for i in range(macd_valid_start, n):
            if macd_line[i] is not None:
                signal_line[i] = ema9_macd[si] if si < len(ema9_macd) else None
                si += 1

        # Volume 24h rolling mean (1440 1m candles)
        vol_window = 1440
        vol_ma: list[float] = []
        for i in range(n):
            start = max(0, i - vol_window)
            vol_ma.append(sum(volumes[start:i + 1]) / (i - start + 1))

        # VWAP (session reset at UTC midnight)
        vwap: list[float] = [0.0] * n
        cum_pv = cum_v = 0.0
        last_day = -1
        for i in range(n):
            day = datetime.fromtimestamp(times[i] / 1000, tz=timezone.utc).day
            if day != last_day:
                cum_pv = cum_v = 0.0
                last_day = day
            typical = (highs[i] + lows[i] + closes[i]) / 3.0
            cum_pv += typical * volumes[i]
            cum_v += volumes[i]
            vwap[i] = cum_pv / cum_v if cum_v > 0 else closes[i]

        # Warm-up: skip first 60 candles (need data for all indicators)
        WARMUP = 60
        daily_pnl = 0.0
        last_reset_day = datetime.fromtimestamp(times[WARMUP] / 1000, tz=timezone.utc).date()

        for i in range(WARMUP, n):
            price = closes[i]
            self.balance_curve.append(self.balance)

            # Daily reset
            today = datetime.fromtimestamp(times[i] / 1000, tz=timezone.utc).date()
            if today != last_reset_day:
                daily_pnl = 0.0
                last_reset_day = today

            # Check SL / TP / liquidation on open position
            if self.position is not None:
                closed = self._check_exits(i, highs[i], lows[i], price)
                if closed:
                    daily_pnl += closed.pnl_usd

            # Daily loss limit guard
            if self.balance > 0 and abs(daily_pnl) / self.balance >= self.daily_loss_limit:
                continue

            # Skip if position already open
            if self.position is not None:
                continue

            # Compute ATR and BB at this index
            atr = _atr_at(highs, lows, closes, i, 14)
            if atr == 0:
                continue

            bb_upper, bb_mid, bb_lower, bb_pos = _bb_position_pct(closes, i, 20, 2.0)
            rsi = _rsi_at(closes, i, 14)

            # Need all EMAs
            if any(v is None for v in [ema9[i], ema21[i], ema50[i]]):
                continue
            if macd_line[i] is None or signal_line[i] is None:
                continue

            # EMA alignment
            if ema9[i] > ema21[i] > ema50[i]:
                ema_alignment = "BULLISH"
            elif ema9[i] < ema21[i] < ema50[i]:
                ema_alignment = "BEARISH"
            else:
                ema_alignment = "MIXED"

            macd_hist = macd_line[i] - signal_line[i]

            # Volume
            vol_ratio = volumes[i] / vol_ma[i] if vol_ma[i] > 0 else 1.0

            # Trend (5-candle momentum)
            trend_window = min(5, i)
            change_pct = (price - closes[i - trend_window]) / closes[i - trend_window] * 100
            if change_pct > 0.1:
                trend, trend_strength = "UP", min(abs(change_pct) / 1.0, 1.0)
            elif change_pct < -0.1:
                trend, trend_strength = "DOWN", min(abs(change_pct) / 1.0, 1.0)
            else:
                trend, trend_strength = "SIDEWAYS", 0.0

            # Approximate buy/sell ratio from candle body
            body = closes[i] - opens[i]
            candle_range = highs[i] - lows[i] or 1.0
            bsr = max(0.1, min(0.9, 0.5 + body / candle_range * 0.4))

            snapshot = {
                "price": {
                    "current": price,
                    "trend": trend,
                    "trend_strength": trend_strength,
                    "change_5m_pct": change_pct,
                },
                "volume": {
                    "volume_ratio": vol_ratio,
                    "buy_sell_ratio": bsr,
                    "is_spike": vol_ratio > 2.0,
                },
                "indicators": {
                    "rsi_14": rsi,
                    "rsi_signal": (
                        "OVERBOUGHT" if rsi > 70 else
                        "OVERSOLD" if rsi < 30 else "NEUTRAL"
                    ),
                    "ema_alignment": ema_alignment,
                    "macd_value": macd_line[i],
                    "macd_signal": signal_line[i],
                    "macd_histogram": macd_hist,
                    "bb_upper": bb_upper,
                    "bb_middle": bb_mid,
                    "bb_lower": bb_lower,
                    "bb_position_pct": bb_pos,
                    "ema_9": ema9[i],
                    "ema_21": ema21[i],
                    "ema_50": ema50[i],
                    "atr_14": atr,
                    "vwap": vwap[i],
                },
                "anomaly_detection": {
                    "is_anomaly": False,
                    "anomaly_score": 0.0,
                },
            }

            result = self.math_engine.analyze(snapshot)
            signal = result["signal"]
            composite = result["composite_score"]
            long_conf = composite / 100
            short_conf = (100 - composite) / 100

            # VWAP directional filter — mirrors decision_engine._determine_action()
            price_above_vwap = closes[i] > vwap[i] if vwap[i] > 0 else True
            price_below_vwap = closes[i] < vwap[i] if vwap[i] > 0 else True

            # Entry conditions — STRONG signal → full risk, WEAK signal → half risk
            if signal == "STRONG_LONG" and long_conf > 0.65 and price_above_vwap:
                self._open_position("LONG", price, atr, i, risk_scale=1.0)
            elif signal == "WEAK_LONG" and long_conf > 0.62 and price_above_vwap:
                self._open_position("LONG", price, atr, i, risk_scale=0.5)
            elif signal == "STRONG_SHORT" and short_conf > 0.65 and price_below_vwap:
                self._open_position("SHORT", price, atr, i, risk_scale=1.0)
            elif signal == "WEAK_SHORT" and short_conf > 0.62 and price_below_vwap:
                self._open_position("SHORT", price, atr, i, risk_scale=0.5)

        # Close any remaining position at last price
        if self.position is not None:
            self._force_close(n - 1, closes[n - 1], "END_OF_DATA")

        return self._build_report(times)

    def _open_position(self, side: str, price: float, atr: float, idx: int, risk_scale: float = 1.0):
        if side == "LONG":
            sl = price - atr * self.sl_atr_mult
            tp = price + atr * self.tp_atr_mult
            liq = price * (1.0 - (1.0 - MAINTENANCE_BUFFER) / self.max_leverage)
        else:
            sl = price + atr * self.sl_atr_mult
            tp = price - atr * self.tp_atr_mult
            liq = price * (1.0 + (1.0 - MAINTENANCE_BUFFER) / self.max_leverage)

        sl_dist = abs(price - sl) / price
        risk_amount = self.balance * self.risk_per_trade * risk_scale
        notional = min(risk_amount / sl_dist, self.balance * self.max_leverage)

        margin_needed = notional / self.max_leverage
        open_fee = notional * FEE_RATE
        if margin_needed + open_fee > self.balance * 0.95:
            return  # Insufficient margin

        self.balance -= open_fee
        self.position = BacktestPosition(
            side=side,
            entry_price=price,
            entry_idx=idx,
            notional=notional,
            leverage=self.max_leverage,
            stop_loss=sl,
            take_profit=tp,
            liquidation_price=liq,
            open_fee=open_fee,
        )

    def _check_exits(self, idx: int, high: float, low: float, close: float) -> Optional[ClosedTrade]:
        pos = self.position
        if pos is None:
            return None

        reason = None
        exit_price = close

        if pos.side == "LONG":
            if low <= pos.liquidation_price:
                reason, exit_price = "LIQUIDATION", pos.liquidation_price
            elif low <= pos.stop_loss:
                reason, exit_price = "STOP_LOSS", pos.stop_loss
            elif high >= pos.take_profit:
                reason, exit_price = "TAKE_PROFIT", pos.take_profit
        else:
            if high >= pos.liquidation_price:
                reason, exit_price = "LIQUIDATION", pos.liquidation_price
            elif high >= pos.stop_loss:
                reason, exit_price = "STOP_LOSS", pos.stop_loss
            elif low <= pos.take_profit:
                reason, exit_price = "TAKE_PROFIT", pos.take_profit

        if reason:
            return self._close_position(exit_price, idx, reason)
        return None

    def _close_position(self, price: float, idx: int, reason: str) -> ClosedTrade:
        pos = self.position
        if pos.side == "LONG":
            pnl_pct = (price - pos.entry_price) / pos.entry_price
        else:
            pnl_pct = (pos.entry_price - price) / pos.entry_price

        pnl_usd = pos.notional * pnl_pct
        close_fee = pos.notional * FEE_RATE
        net_pnl = pnl_usd - close_fee
        self.balance += net_pnl

        trade = ClosedTrade(
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=price,
            pnl_usd=net_pnl,
            pnl_pct=pnl_pct * 100,
            notional=pos.notional,
            fees_usd=pos.open_fee + close_fee,
            reason=reason,
            entry_idx=pos.entry_idx,
            exit_idx=idx,
        )
        self.trades.append(trade)
        self.position = None
        return trade

    def _force_close(self, idx: int, price: float, reason: str):
        self._close_position(price, idx, reason)

    def _build_report(self, times: list[int]) -> dict:
        trades = self.trades
        if not trades:
            print("\n  No trades executed.")
            return {"trades": 0}

        wins = [t for t in trades if t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd <= 0]
        longs = [t for t in trades if t.side == "LONG"]
        shorts = [t for t in trades if t.side == "SHORT"]

        total_return = (self.balance - self.starting_balance) / self.starting_balance * 100
        win_rate = len(wins) / len(trades) * 100

        gross_wins = sum(t.pnl_usd for t in wins)
        gross_losses = abs(sum(t.pnl_usd for t in losses))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

        avg_win = sum(t.pnl_usd for t in wins) / len(wins) if wins else 0
        avg_win_pct = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_usd for t in losses) / len(losses) if losses else 0
        avg_loss_pct = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0

        best = max(trades, key=lambda t: t.pnl_usd)
        worst = min(trades, key=lambda t: t.pnl_usd)
        total_fees = sum(t.fees_usd for t in trades)
        liquidations = sum(1 for t in trades if t.reason == "LIQUIDATION")

        # Max drawdown from balance curve
        peak = self.starting_balance
        max_dd = 0.0
        for b in self.balance_curve:
            if b > peak:
                peak = b
            dd = (peak - b) / peak * 100
            if dd > max_dd:
                max_dd = dd

        # Sharpe ratio (annualised, per-trade returns)
        if len(trades) > 1:
            returns = [t.pnl_usd / self.starting_balance for t in trades]
            avg_r = sum(returns) / len(returns)
            std_r = math.sqrt(sum((r - avg_r) ** 2 for r in returns) / len(returns))
            trades_per_year = len(trades) / self.days * 365
            sharpe = (avg_r / std_r * math.sqrt(trades_per_year)) if std_r > 0 else 0.0
        else:
            sharpe = 0.0

        # ── Print report ──────────────────────────────────────────────────────
        W = 58
        sep = "─" * W
        eq = "═" * W

        start_dt = datetime.fromtimestamp(times[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        end_dt   = datetime.fromtimestamp(times[-1] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        print(f"\n{eq}")
        print(f"  BACKTEST REPORT — {self.symbol}  ({self.days}d, {self.interval} candles)")
        print(f"{eq}")
        print(f"  Period:          {start_dt} → {end_dt}")
        print(f"  Starting bal:    ${self.starting_balance:,.2f}")
        print(f"  Final bal:       ${self.balance:,.2f}")
        ret_sign = "+" if total_return >= 0 else ""
        print(f"  Total return:    {ret_sign}{total_return:.2f}%")
        print(f"{sep}")
        print(f"  Trades:          {len(trades)}  "
              f"(L: {len(longs)}  S: {len(shorts)})")
        print(f"  Win rate:        {win_rate:.1f}%  "
              f"({len(wins)}W / {len(losses)}L)")
        print(f"  Profit factor:   {profit_factor:.2f}")
        print(f"  Avg win:         +${avg_win:,.2f}  ({avg_win_pct:+.2f}%)")
        print(f"  Avg loss:        ${avg_loss:,.2f}  ({avg_loss_pct:+.2f}%)")
        print(f"  Best trade:      +${best.pnl_usd:,.2f}  ({best.pnl_pct:+.2f}%)")
        print(f"  Worst trade:     ${worst.pnl_usd:,.2f}  ({worst.pnl_pct:+.2f}%)")
        print(f"{sep}")
        print(f"  Max drawdown:    -{max_dd:.2f}%")
        print(f"  Sharpe ratio:    {sharpe:.2f}")
        print(f"  Total fees:      ${total_fees:,.2f}")
        print(f"  Liquidations:    {liquidations}")
        print(f"{sep}")

        # Exit reason breakdown
        reasons: dict[str, int] = {}
        for t in trades:
            reasons[t.reason] = reasons.get(t.reason, 0) + 1
        print("  Exit breakdown:")
        for reason, count in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason:<20} {count:>4}x")
        print(f"{eq}\n")

        return {
            "symbol": self.symbol,
            "days": self.days,
            "starting_balance": self.starting_balance,
            "final_balance": round(self.balance, 2),
            "total_return_pct": round(total_return, 2),
            "trades": len(trades),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(sharpe, 2),
            "total_fees_usd": round(total_fees, 2),
            "liquidations": liquidations,
        }


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CryptoSentinelTrader Backtester")
    parser.add_argument("--symbol",   default="BTCUSDT",  help="Trading pair (default: BTCUSDT)")
    parser.add_argument("--days",     type=int, default=30, help="Number of days to backtest (default: 30)")
    parser.add_argument("--interval", default="1m",        help="Candle interval (default: 1m)")
    parser.add_argument("--balance",  type=float, default=1000.0, help="Starting balance in USD")
    parser.add_argument("--risk",     type=float, default=0.03,   help="Risk per trade 0-1 (default: 0.03)")
    parser.add_argument("--leverage", type=int,   default=5,      help="Max leverage (default: 5)")
    parser.add_argument("--json",     type=str,   default=None,
                        help="Write a track-record BacktestResult card to this JSON path (additive export).")
    parser.add_argument("--period-label", type=str, default=None,
                        help="Human label for the backtest card, e.g. '90d chop'. Defaults to symbol/days.")
    args = parser.parse_args()

    bt = Backtester(
        symbol=args.symbol,
        days=args.days,
        interval=args.interval,
        starting_balance=args.balance,
        risk_per_trade=args.risk,
        max_leverage=args.leverage,
    )
    result = bt.run()

    if args.json:
        # Map the report into the track_record BacktestResult shape (see
        # track_record/models.py). Note: report stores max_drawdown_pct as a
        # positive magnitude; the card expects a signed (negative) value.
        import json as _json
        card = {
            "period": args.period_label or f"{args.symbol} {args.days}d",
            "profit_factor": result.get("profit_factor"),
            "win_rate_pct": result.get("win_rate_pct"),
            "max_dd_pct": -abs(result["max_drawdown_pct"]) if result.get("max_drawdown_pct") is not None else None,
            "note": f"{result.get('trades', 0)} trades · {result.get('total_return_pct', 0):+.2f}% · "
                    f"Sharpe {result.get('sharpe_ratio', 0)}",
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            _json.dump([card], fh, indent=2)
        print(f"📄 Wrote backtest card → {args.json}")


if __name__ == "__main__":
    main()
