"""
Trade Executor — Handles order placement, position management, and risk enforcement.
Supports paper trading (simulation) and live trading (via ccxt).
Features: Trailing stop (watermark-based), ATR-based SL/TP sizing, fee simulation,
          liquidation checks, max position limits, daily loss reset,
          position persistence (crash recovery), exchange sync, price watchdog.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sentinel.executor")

STATE_FILE = Path(__file__).parent.parent / "data" / "positions.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Position:
    """An open position with trailing stop state"""
    id: str
    symbol: str
    side: str               # "LONG" or "SHORT"
    entry_price: float
    size_usd: float         # Notional (leveraged) position size in USD
    leverage: float
    stop_loss: float
    take_profit: float
    liquidation_price: float
    opened_at: str
    status: str = "OPEN"
    watermark: float = 0.0          # Best price seen (high for LONG, low for SHORT)
    highest_pnl_pct: float = 0.0
    exit_price: Optional[float] = None
    pnl_usd: Optional[float] = None
    pnl_pct: Optional[float] = None
    close_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class TradeExecutor:
    """Execute trading decisions with adaptive risk management"""

    # Taker fee on Binance Futures: 0.05% per side
    DEFAULT_FEE_RATE = 0.0005

    def __init__(self, config: dict, mode: str = "paper"):
        self.config = config
        self.mode = mode
        self.balance = config.get("paper", {}).get("starting_balance", 1000.0)
        self.positions: list[Position] = []
        self.trade_history: list[dict] = []

        self.daily_pnl = 0.0
        self.daily_fees = 0.0
        self._daily_reset_date = _utcnow().date()

        self.daily_loss_limit = config.get("risk", {}).get("daily_loss_limit_pct", 0.10)
        self.max_open_positions = config.get("risk", {}).get("max_open_positions", 3)
        self.fee_rate = config.get("exchange", {}).get("trading_fee_pct", self.DEFAULT_FEE_RATE)

        self.exchange = None
        self._last_close_time: dict[str, datetime] = {}  # symbol -> last close time (anti-whipsaw)
        self._cooldown_sec = 900  # 15 min cooldown after closing before re-entry

        # Rolling Kelly: track last N trade results for dynamic position sizing
        self._trade_results: list[float] = []  # PnL percentages of closed trades
        self._max_trade_history = 50  # Rolling window

        if mode == "live":
            self._init_exchange()

        # Restore state from disk (crash recovery)
        self._load_state()

        logger.info(
            "Executor initialized | Mode: %s | Balance: $%.2f | Fee: %.3f%% | Restored positions: %d",
            self.mode, self.balance, self.fee_rate * 100, len(self.positions),
        )

    # ── Exchange init ─────────────────────────────────────────────────────────

    def _init_exchange(self):
        """Initialize exchange connection via ccxt"""
        try:
            import ccxt

            exchange_id = self.config.get("exchange", {}).get("primary", "binance")
            exchange_map = {
                "binance_futures": "binance",
                "binance_futures_testnet": "binance",
                "bybit": "bybit",
                "hyperliquid": "hyperliquid",
            }
            ccxt_id = exchange_map.get(exchange_id, exchange_id)
            exchange_class = getattr(ccxt, ccxt_id)

            self.exchange = exchange_class({
                "apiKey": self.config["exchange"].get("api_key", ""),
                "secret": self.config["exchange"].get("api_secret", ""),
                "options": {"defaultType": "future"},
                "sandbox": "testnet" in exchange_id,
            })
            logger.info("Exchange connected: %s (sandbox: %s)", ccxt_id, "testnet" in exchange_id)

        except Exception as e:
            logger.error("Failed to init exchange: %s — falling back to paper mode", e)
            self.mode = "paper"

    # ── State persistence ────────────────────────────────────────────────────

    def _save_state(self) -> None:
        """Persist positions and balance to disk for crash recovery."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "balance": self.balance,
            "daily_pnl": self.daily_pnl,
            "daily_fees": self.daily_fees,
            "daily_reset_date": self._daily_reset_date.isoformat(),
            "positions": [p.to_dict() for p in self.positions],
            "saved_at": _utcnow().isoformat(),
        }
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str))
        tmp.rename(STATE_FILE)

    def _load_state(self) -> None:
        """Restore positions and balance from disk."""
        if not STATE_FILE.exists():
            return
        try:
            state = json.loads(STATE_FILE.read_text())
            self.balance = state.get("balance", self.balance)
            self.daily_pnl = state.get("daily_pnl", 0.0)
            self.daily_fees = state.get("daily_fees", 0.0)

            saved_date = state.get("daily_reset_date", "")
            if saved_date:
                from datetime import date
                self._daily_reset_date = date.fromisoformat(saved_date)

            for pos_data in state.get("positions", []):
                if pos_data.get("status") == "OPEN":
                    self.positions.append(Position.from_dict(pos_data))

            if self.positions:
                logger.info(
                    "Restored %d open position(s) from disk: %s",
                    len(self.positions),
                    [f"{p.symbol} {p.side}" for p in self.positions],
                )
        except Exception as e:
            logger.error("Failed to load state from %s: %s", STATE_FILE, e)

    # ── Exchange sync ────────────────────────────────────────────────────────

    async def sync_with_exchange(self) -> None:
        """Sync local position state with actual exchange positions (live mode only)."""
        if self.mode != "live" or self.exchange is None:
            return

        try:
            exchange_positions = self.exchange.fetch_positions()
            active = [p for p in exchange_positions if abs(float(p.get("contracts", 0))) > 0]

            if not active and self.positions:
                logger.warning(
                    "Exchange has 0 open positions but we track %d — clearing local state",
                    len(self.positions),
                )
                self.positions.clear()
                self._save_state()
                return

            exchange_symbols = set()
            for ep in active:
                sym = ep.get("symbol", "")
                exchange_symbols.add(sym)
                side = "LONG" if float(ep.get("contracts", 0)) > 0 else "SHORT"
                entry = float(ep.get("entryPrice", 0))

                # Check if we already track this position
                local = [p for p in self.positions if self._ccxt_symbol(p.symbol) == sym]
                if not local and entry > 0:
                    # Exchange has a position we don't know about — adopt it
                    logger.warning(
                        "Found untracked exchange position: %s %s @ %.2f — adopting",
                        side, sym, entry,
                    )
                    notional = abs(float(ep.get("notional", 0)))
                    liq = float(ep.get("liquidationPrice", 0) or 0)
                    pos = Position(
                        id=f"pos_synced_{_utcnow().strftime('%Y%m%d_%H%M%S')}",
                        symbol=sym.replace("/", "").replace(":USDT", ""),
                        side=side,
                        entry_price=entry,
                        size_usd=notional,
                        leverage=float(ep.get("leverage", 1)),
                        stop_loss=entry * (0.97 if side == "LONG" else 1.03),
                        take_profit=entry * (1.06 if side == "LONG" else 0.94),
                        liquidation_price=liq,
                        opened_at=_utcnow().isoformat(),
                        watermark=entry,
                    )
                    self.positions.append(pos)

            # Remove local positions that no longer exist on exchange
            self.positions = [
                p for p in self.positions
                if self._ccxt_symbol(p.symbol) in exchange_symbols
            ]

            self._save_state()
            logger.info(
                "Exchange sync complete: %d positions on exchange, %d tracked locally",
                len(active), len(self.positions),
            )

        except Exception as e:
            logger.error("Exchange sync failed: %s", e)

    # ── Price watchdog ───────────────────────────────────────────────────────

    async def run_watchdog(self, interval_sec: int = 5) -> None:
        """
        Independent price watchdog — checks SL/TP/liquidation even when
        no snapshots are arriving (Rust restart, network issues).
        Uses ccxt ticker in live mode, skips in paper mode (no independent
        price source available).
        """
        if self.mode != "live" or self.exchange is None:
            # Paper mode watchdog: just persist state periodically
            while True:
                if self.positions:
                    self._save_state()
                await asyncio.sleep(interval_sec)
                return  # Paper mode has no independent price source

        logger.info("Watchdog started (interval: %ds)", interval_sec)
        while True:
            try:
                if self.positions:
                    await self._watchdog_check()
            except Exception as e:
                logger.error("Watchdog error: %s", e)
            await asyncio.sleep(interval_sec)

    async def _watchdog_check(self) -> None:
        """Fetch latest prices from exchange and check exit conditions."""
        # Collect unique symbols we need prices for
        symbols = list({p.symbol for p in self.positions})
        for symbol in symbols:
            try:
                ticker = self.exchange.fetch_ticker(self._ccxt_symbol(symbol))
                price = ticker.get("last", 0)
                if price <= 0:
                    continue

                # Check exits for all positions with this symbol
                for pos in list(self.positions):
                    if pos.symbol != symbol or pos.status != "OPEN":
                        continue

                    hit = False
                    if pos.side == "LONG":
                        if price <= pos.liquidation_price:
                            await self._close_specific_position(pos, price, {}, reason="LIQUIDATION")
                            hit = True
                        elif price <= pos.stop_loss:
                            await self._close_specific_position(pos, price, {}, reason="STOP_LOSS")
                            hit = True
                        elif price >= pos.take_profit:
                            await self._close_specific_position(pos, price, {}, reason="TAKE_PROFIT")
                            hit = True
                    else:
                        if price >= pos.liquidation_price:
                            await self._close_specific_position(pos, price, {}, reason="LIQUIDATION")
                            hit = True
                        elif price >= pos.stop_loss:
                            await self._close_specific_position(pos, price, {}, reason="STOP_LOSS")
                            hit = True
                        elif price <= pos.take_profit:
                            await self._close_specific_position(pos, price, {}, reason="TAKE_PROFIT")
                            hit = True

                    if hit:
                        logger.warning("[WATCHDOG] Closed %s %s — snapshot pipeline was not active", pos.side, symbol)

            except Exception as e:
                logger.warning("Watchdog ticker fetch failed for %s: %s", symbol, e)

    # ── Public API ────────────────────────────────────────────────────────────

    async def execute(self, decision: dict, snapshot: dict) -> dict:
        """Execute a trading decision with trailing stop updates"""
        self._maybe_reset_daily_stats()

        action = decision.get("decision", {}).get("action", "HOLD")
        risk = decision.get("risk_parameters", {})
        price = snapshot.get("price", {}).get("current", 0)
        symbol = snapshot.get("symbol", "BTCUSDT")

        # Update trailing stops on all open positions
        self._update_trailing_stops(price, snapshot)

        # Check SL / TP / liquidation exit conditions
        await self._check_exit_conditions(price, snapshot)

        # Daily loss limit guard — only block on actual losses, not profits
        if self.daily_pnl < 0 and self.balance > 0 and abs(self.daily_pnl) / self.balance >= self.daily_loss_limit:
            logger.warning(
                "Daily loss limit reached (%.1f%%). No new trades.",
                abs(self.daily_pnl) / self.balance * 100,
            )
            return {"status": "BLOCKED", "reason": "daily_loss_limit"}

        if action == "ENTER_LONG":
            return await self._open_position("LONG", price, risk, snapshot, symbol)
        elif action == "ENTER_SHORT":
            return await self._open_position("SHORT", price, risk, snapshot, symbol)
        elif action == "EXIT":
            return await self._close_all_open(price, snapshot, reason="SIGNAL_EXIT", symbol=symbol)
        elif action == "STOP":
            return await self.close_all_positions(price, symbol=symbol)
        else:
            return {"status": "NO_ACTION", "action": action}

    async def adjust_position(self, decision: dict, snapshot: dict) -> dict:
        """Trailing stops update automatically via execute(); nothing extra needed."""
        return {"status": "TRAILING_STOP_ACTIVE"}

    async def close_all_positions(self, last_price: float = 0.0, symbol: str = "") -> dict:
        """Emergency close positions. Uses entry_price for cross-symbol safety."""
        closed = 0
        for pos in list(self.positions):
            # Use last_price ONLY if it matches the position's symbol
            if symbol and pos.symbol == symbol and last_price > 0:
                price = last_price
            else:
                # Use entry price (break-even) for safety — never use a different symbol's price
                price = pos.entry_price
            await self._close_specific_position(pos, price, {}, reason="EMERGENCY")
            closed += 1
        logger.warning("Emergency close: %d positions closed", closed)
        return {"status": "ALL_CLOSED", "count": closed}

    def get_position_info(self, symbol: str = "") -> dict:
        """Return open position summary for a specific symbol (or first position if no symbol given)"""
        if symbol:
            matching = [p for p in self.positions if p.symbol == symbol]
        else:
            matching = self.positions

        if not matching:
            return {"has_position": False}
        pos = matching[0]
        return {
            "has_position": True,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "position_id": pos.id,
            "symbol": pos.symbol,
            "current_pnl_pct": 0.0,
            "opened_at": pos.opened_at,
        }

    # ── Daily reset ───────────────────────────────────────────────────────────

    def _maybe_reset_daily_stats(self):
        """Reset daily PnL and fee counters at UTC midnight"""
        today = _utcnow().date()
        if today != self._daily_reset_date:
            logger.info(
                "New day — resetting daily stats (PnL: $%.2f, Fees: $%.2f)",
                self.daily_pnl, self.daily_fees,
            )
            self.daily_pnl = 0.0
            self.daily_fees = 0.0
            self._daily_reset_date = today

    # ── Position opening ──────────────────────────────────────────────────────

    async def _open_position(
        self, side: str, price: float, risk: dict, snapshot: dict, symbol: str
    ) -> dict:
        """Open a new position with ATR-based SL/TP, fee deduction, and liquidation price"""

        # Max positions guard
        if len(self.positions) >= self.max_open_positions:
            logger.warning("Max open positions reached (%d)", self.max_open_positions)
            return {"status": "REJECTED", "reason": "max_positions_reached"}

        # Anti-whipsaw cooldown: don't re-enter same symbol too quickly
        if symbol in self._last_close_time:
            elapsed = (_utcnow() - self._last_close_time[symbol]).total_seconds()
            if elapsed < self._cooldown_sec:
                remaining = int(self._cooldown_sec - elapsed)
                logger.debug("Cooldown active for %s: %ds remaining", symbol, remaining)
                return {"status": "REJECTED", "reason": f"cooldown_{remaining}s"}

        # Don't open opposite position on same symbol if already have one
        existing = [p for p in self.positions if p.symbol == symbol]
        if existing:
            logger.debug("Already has position for %s — skipping", symbol)
            return {"status": "REJECTED", "reason": "already_has_position"}

        risk_pct = 3.0 # Default 3%
        leverage = float(risk.get("max_leverage", 5))
        confidence_tier = risk.get("confidence_tier", "MEDIUM")

        # Dynamic risk sizing based on confidence
        if confidence_tier == "HIGH":
            risk_pct = 5.0 # Risk 5% on strong signals
        elif confidence_tier == "LOW":
            risk_pct = 1.5 # Risk only 1.5% on opportunistic signals
        else:
            risk_pct = 3.0 # Standard 3% for medium signals

        risk_pct = risk_pct / 100 # Convert to decimal

        # Rolling Kelly override: if we have enough trade history, use actual edge
        kelly_f = self._get_kelly_fraction()
        if kelly_f > 0:
            risk_pct = kelly_f
            logger.info("Rolling Kelly active: risk=%.1f%% (from %d trades)", kelly_f * 100, len(self._trade_results))

        atr = self._get_atr(snapshot, price)
        # Wider SL gives trades room to breathe; tight SL = constant stop-outs in volatile crypto
        sl_mult, tp_mult = (2.5, 6.0) if confidence_tier == "HIGH" else (2.0, 5.0)

        # SL / TP prices
        if side == "LONG":
            stop_loss = price - atr * sl_mult
            take_profit = price + atr * tp_mult
        else:
            stop_loss = price + atr * sl_mult
            take_profit = price - atr * tp_mult

        # Notional position size: sized so that hitting SL = losing risk_amount
        sl_distance = abs(price - stop_loss)
        sl_pct = sl_distance / price if price > 0 else 0.01
        risk_amount = self.balance * risk_pct
        # Cap notional: max 50% of balance as margin per single position
        max_margin = self.balance * 0.5
        max_notional = max_margin * leverage
        notional = min(risk_amount / sl_pct, max_notional)

        # Margin check: must have enough free margin (max 50% of balance per position)
        margin_needed = notional / leverage
        open_fee = notional * self.fee_rate
        total_needed = margin_needed + open_fee
        max_per_position = self.balance * 0.50
        if total_needed > max_per_position:
            # Scale down notional to fit within margin limit
            notional = (max_per_position * leverage) / (1 + self.fee_rate * leverage)
            margin_needed = notional / leverage
            open_fee = notional * self.fee_rate
            # Recalculate SL/TP with new notional
            if side == "LONG":
                stop_loss = price - (self.balance * risk_pct / notional) * price
                take_profit = price + (self.balance * risk_pct / notional) * price * (tp_mult / sl_mult)
            else:
                stop_loss = price + (self.balance * risk_pct / notional) * price
                take_profit = price - (self.balance * risk_pct / notional) * price * (tp_mult / sl_mult)
            logger.info(
                "Margin scaled down: notional $%.2f | margin $%.2f + fee $%.2f",
                notional, margin_needed, open_fee,
            )

        # Final safety check: must have enough balance for margin + fee
        final_needed = margin_needed + open_fee
        if final_needed > self.balance:
            logger.warning(
                "Insufficient balance: need $%.2f, have $%.2f",
                final_needed, self.balance,
            )
            return {"status": "REJECTED", "reason": "insufficient_margin"}

        # Liquidation price (simplified: lose full margin = 1/leverage drop/rise)
        maintenance_buffer = 0.05  # 5% maintenance margin
        if side == "LONG":
            liq_price = price * (1.0 - (1.0 - maintenance_buffer) / leverage)
        else:
            liq_price = price * (1.0 + (1.0 - maintenance_buffer) / leverage)

        # Deduct opening fee immediately
        self.balance -= open_fee
        self.daily_fees += open_fee

        pos = Position(
            id=f"pos_{_utcnow().strftime('%Y%m%d_%H%M%S')}",
            symbol=symbol,
            side=side,
            entry_price=price,
            size_usd=round(notional, 2),
            leverage=leverage,
            stop_loss=round(stop_loss, 2),
            take_profit=round(take_profit, 2),
            liquidation_price=round(liq_price, 2),
            opened_at=_utcnow().isoformat(),
            watermark=price,
        )

        if self.mode == "paper":
            self.positions.append(pos)
            logger.info(
                "[PAPER] %s %s | notional: $%.0f | %dx | entry: $%.2f | "
                "SL: $%.2f | TP: $%.2f | liq: $%.2f | fee: $%.2f",
                side, symbol, notional, int(leverage), price,
                pos.stop_loss, pos.take_profit, pos.liquidation_price, open_fee,
            )
        else:
            try:
                order = self.exchange.create_order(
                    symbol=self._ccxt_symbol(symbol),
                    type="market",
                    side="buy" if side == "LONG" else "sell",
                    amount=round(notional / price, 6),
                    params={"leverage": int(leverage)},
                )
                self.positions.append(pos)
                logger.info(
                    "[LIVE] %s %s | order_id: %s | notional: $%.0f | %dx",
                    side, symbol, order.get("id", "?"), notional, int(leverage),
                )
            except Exception as e:
                # Refund opening fee on order failure
                self.balance += open_fee
                self.daily_fees -= open_fee
                logger.error("[LIVE] Order placement failed: %s", e)
                return {"status": "ORDER_FAILED", "error": str(e)}

        self.trade_history.append({**pos.to_dict(), "type": "OPEN", "fee_usd": round(open_fee, 4)})
        self._save_state()
        return {"status": "OPENED", "position": pos.to_dict(), "fee_usd": round(open_fee, 4)}

    # ── Position closing ──────────────────────────────────────────────────────

    async def _close_specific_position(
        self, position: Position, price: float, snapshot: dict, reason: str = "MANUAL"
    ) -> dict:
        """Close a specific position, deduct closing fee, and update balance"""
        if position.side == "LONG":
            pnl_pct = (price - position.entry_price) / position.entry_price
        else:
            pnl_pct = (position.entry_price - price) / position.entry_price

        pnl_usd = position.size_usd * pnl_pct
        close_fee = position.size_usd * self.fee_rate

        net_pnl = pnl_usd - close_fee
        self.balance += net_pnl
        self.daily_pnl += net_pnl
        self.daily_fees += close_fee

        position.status = "CLOSED"
        position.exit_price = price
        position.pnl_usd = round(net_pnl, 2)

        # Record close time for anti-whipsaw cooldown
        self._last_close_time[position.symbol] = _utcnow()

        # Record PnL for Rolling Kelly calculation
        self._trade_results.append(pnl_pct * 100)
        if len(self._trade_results) > self._max_trade_history:
            self._trade_results = self._trade_results[-self._max_trade_history:]

        position.pnl_pct = round(pnl_pct * 100, 2)
        position.close_reason = reason

        self.positions.remove(position)

        logger.info(
            "[%s] Closed %s %s | Reason: %s | PnL: $%.2f (%.2f%%) | fee: $%.2f | Balance: $%.2f",
            self.mode.upper(), position.side, position.symbol, reason,
            net_pnl, pnl_pct * 100, close_fee, self.balance,
        )

        if self.mode == "live" and self.exchange is not None:
            try:
                self.exchange.create_order(
                    symbol=self._ccxt_symbol(position.symbol),
                    type="market",
                    side="sell" if position.side == "LONG" else "buy",
                    amount=round(position.size_usd / price, 6),
                )
            except Exception as e:
                logger.error("[LIVE] Close order failed: %s", e)

        self.trade_history.append({
            **position.to_dict(), "type": "CLOSE",
            "close_fee_usd": round(close_fee, 4),
        })
        self._save_state()
        return {
            "status": "CLOSED",
            "reason": reason,
            "pnl_usd": net_pnl,
            "pnl_pct": pnl_pct * 100,
            "fee_usd": round(close_fee, 4),
            "balance": self.balance,
        }

    async def _close_all_open(
        self, price: float, snapshot: dict, reason: str = "SIGNAL_EXIT", symbol: str = ""
    ) -> dict:
        """Close open positions, optionally filtered by symbol"""
        targets = list(self.positions)
        if symbol:
            targets = [p for p in targets if p.symbol == symbol]
        if not targets:
            return {"status": "NO_POSITION"}
        results = []
        for pos in targets:
            r = await self._close_specific_position(pos, price, snapshot, reason=reason)
            results.append(r)
        return {"status": "CLOSED", "count": len(results), "results": results}

    # ── Trailing stop engine ──────────────────────────────────────────────────

    def _update_trailing_stops(self, price: float, snapshot: dict) -> None:
        """
        Watermark-based trailing stop:
        - Track the best price (watermark) the trade has reached
        - Move SL toward watermark to lock in gains — SL never moves backward
        Only updates positions matching the current snapshot's symbol.
        """
        symbol = snapshot.get("symbol", "")
        atr = self._get_atr(snapshot, price)
        # Kurtosis-aware trailing: widen trail multiplier during fat-tail regimes
        kurtosis_mult = snapshot.get("_kurtosis_atr_mult", 1.0)
        trail_mult = 1.5 * kurtosis_mult
        changed = False

        for pos in self.positions:
            if pos.status != "OPEN":
                continue
            if pos.symbol != symbol:
                continue

            if pos.side == "LONG":
                if price > pos.watermark:
                    pos.watermark = price
                    changed = True
                trailing_sl = pos.watermark - atr * trail_mult
                if trailing_sl > pos.stop_loss:
                    pos.stop_loss = round(trailing_sl, 2)
                    changed = True

                current_pnl_pct = (pos.watermark - pos.entry_price) / pos.entry_price * 100
            else:
                if price < pos.watermark:
                    pos.watermark = price
                    changed = True
                trailing_sl = pos.watermark + atr * trail_mult
                if trailing_sl < pos.stop_loss:
                    pos.stop_loss = round(trailing_sl, 2)
                    changed = True

                current_pnl_pct = (pos.entry_price - pos.watermark) / pos.entry_price * 100

            if current_pnl_pct > pos.highest_pnl_pct:
                pos.highest_pnl_pct = round(current_pnl_pct, 2)
                changed = True

        if changed:
            self._save_state()

    async def _check_exit_conditions(self, price: float, snapshot: dict) -> None:
        """Close positions whose SL, TP, or liquidation price has been hit.
        Only checks the position matching the current snapshot's symbol."""
        symbol = snapshot.get("symbol", "")
        for pos in list(self.positions):
            if pos.status != "OPEN":
                continue
            # Only check exit conditions for the symbol in this snapshot
            if pos.symbol != symbol:
                continue

            if pos.side == "LONG":
                if price <= pos.liquidation_price:
                    await self._close_specific_position(pos, price, snapshot, reason="LIQUIDATION")
                elif price <= pos.stop_loss:
                    await self._close_specific_position(pos, price, snapshot, reason="STOP_LOSS")
                elif price >= pos.take_profit:
                    await self._close_specific_position(pos, price, snapshot, reason="TAKE_PROFIT")
            else:
                if price >= pos.liquidation_price:
                    await self._close_specific_position(pos, price, snapshot, reason="LIQUIDATION")
                elif price >= pos.stop_loss:
                    await self._close_specific_position(pos, price, snapshot, reason="STOP_LOSS")
                elif price <= pos.take_profit:
                    await self._close_specific_position(pos, price, snapshot, reason="TAKE_PROFIT")

    # ── Rolling Kelly ──────────────────────────────────────────────────────

    def _get_kelly_fraction(self) -> float:
        """Rolling Kelly criterion from actual trade results.
        Uses half-Kelly for safety (from AIHedgeFund).
        Returns fraction of balance to risk (0.01 - 0.10)."""
        if len(self._trade_results) < 10:
            return 0.0  # Not enough data, use config risk tiers instead

        wins = [r for r in self._trade_results if r > 0]
        losses = [r for r in self._trade_results if r < 0]

        if not wins or not losses:
            return 0.0

        win_rate = len(wins) / len(self._trade_results)
        avg_win = sum(wins) / len(wins)
        avg_loss = abs(sum(losses) / len(losses))

        if avg_loss <= 0:
            return 0.0

        # Kelly: f* = (b*p - q) / b where b = avg_win/avg_loss
        b = avg_win / avg_loss
        f_star = (b * win_rate - (1 - win_rate)) / b

        # Half-Kelly for safety, clamped to [0.01, 0.10]
        half_kelly = f_star * 0.5
        return max(0.01, min(0.10, half_kelly))

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_atr(snapshot: dict, price: float) -> float:
        """Extract ATR from snapshot, with a sane price-relative fallback"""
        atr = snapshot.get("indicators", {}).get("atr_14", 0.0)
        if atr < price * 0.0001:
            atr = price * 0.01  # Default: 1% of price
        return atr

    @staticmethod
    def _ccxt_symbol(symbol: str) -> str:
        """Convert 'BTCUSDT' -> 'BTC/USDT' for ccxt"""
        if "/" in symbol:
            return symbol
        for quote in ("USDT", "BUSD", "USDC", "BTC", "ETH"):
            if symbol.endswith(quote):
                return symbol[: -len(quote)] + "/" + quote
        return symbol
