"""
Trade Executor — Handles order placement, position management, and risk enforcement.
Supports paper trading (simulation) and live trading (via ccxt).
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("sentinel.executor")


class TradeExecutor:
    """Execute trading decisions with adaptive risk management"""

    def __init__(self, config: dict, mode: str = "paper"):
        self.config = config
        self.mode = mode
        self.balance = config.get("paper", {}).get("starting_balance", 1000.0)
        self.positions = []
        self.trade_history = []
        self.daily_pnl = 0.0
        self.daily_loss_limit = config.get("risk", {}).get("daily_loss_limit_pct", 0.10)
        self.exchange = None

        if mode == "live":
            self._init_exchange()

        logger.info(
            "Executor initialized | Mode: %s | Balance: $%.2f",
            self.mode, self.balance,
        )

    def _init_exchange(self):
        """Initialize exchange connection via ccxt"""
        try:
            import ccxt

            exchange_id = self.config.get("exchange", {}).get("primary", "binance")
            # Map config names to ccxt exchange classes
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
            logger.error("Failed to init exchange: %s", e)
            logger.warning("Falling back to paper trading mode")
            self.mode = "paper"

    async def execute(self, decision: dict, snapshot: dict) -> dict:
        """Execute a trading decision"""
        action = decision.get("decision", {}).get("action", "HOLD")
        risk = decision.get("risk_parameters", {})
        price = snapshot.get("price", {}).get("current", 0)

        # Daily loss limit check
        if abs(self.daily_pnl / self.balance) >= self.daily_loss_limit:
            logger.warning("⛔ Daily loss limit reached (%.1f%%). No new trades.", self.daily_pnl / self.balance * 100)
            return {"status": "BLOCKED", "reason": "daily_loss_limit"}

        if action == "ENTER_LONG":
            return await self._open_position("LONG", price, risk, snapshot)
        elif action == "ENTER_SHORT":
            return await self._open_position("SHORT", price, risk, snapshot)
        elif action == "EXIT":
            return await self._close_position(price, snapshot)
        elif action == "STOP":
            return await self.close_all_positions()
        else:
            return {"status": "NO_ACTION", "action": action}

    async def _open_position(self, side: str, price: float, risk: dict, snapshot: dict) -> dict:
        """Open a new position with calculated sizing"""
        risk_pct = risk.get("risk_per_trade_pct", 3.0) / 100
        leverage = risk.get("max_leverage", 5)
        sl_pct = risk.get("stop_loss_distance_pct", 0.5) / 100
        tp_pct = risk.get("take_profit_distance_pct", 1.5) / 100

        # Position sizing: risk amount / stop distance
        risk_amount = self.balance * risk_pct
        position_size = risk_amount / sl_pct if sl_pct > 0 else risk_amount
        position_size = min(position_size, self.balance * leverage)

        # Stop loss and take profit prices
        if side == "LONG":
            stop_loss = price * (1 - sl_pct)
            take_profit = price * (1 + tp_pct)
        else:
            stop_loss = price * (1 + sl_pct)
            take_profit = price * (1 - tp_pct)

        position = {
            "id": f"pos_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
            "side": side,
            "entry_price": price,
            "size_usd": round(position_size, 2),
            "leverage": leverage,
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "opened_at": datetime.utcnow().isoformat(),
            "status": "OPEN",
        }

        if self.mode == "paper":
            self.positions.append(position)
            logger.info(
                "📝 [PAPER] Opened %s | $%.2f @ $%.2f | %dx | SL: $%.2f | TP: $%.2f",
                side, position_size, price, leverage, stop_loss, take_profit,
            )
        else:
            # TODO: Place real order via ccxt
            logger.info("🔴 [LIVE] Would open %s | $%.2f @ $%.2f", side, position_size, price)

        self.trade_history.append({**position, "type": "OPEN"})
        return {"status": "OPENED", "position": position}

    async def _close_position(self, price: float, snapshot: dict) -> dict:
        """Close the current open position"""
        if not self.positions:
            return {"status": "NO_POSITION"}

        position = self.positions[-1]
        entry = position["entry_price"]
        side = position["side"]

        # Calculate PnL
        if side == "LONG":
            pnl_pct = (price - entry) / entry
        else:
            pnl_pct = (entry - price) / entry

        pnl_usd = position["size_usd"] * pnl_pct
        self.balance += pnl_usd
        self.daily_pnl += pnl_usd

        position["status"] = "CLOSED"
        position["exit_price"] = price
        position["pnl_usd"] = round(pnl_usd, 2)
        position["pnl_pct"] = round(pnl_pct * 100, 2)

        self.positions.pop()

        logger.info(
            "📝 [%s] Closed %s | PnL: $%.2f (%.2f%%) | Balance: $%.2f",
            self.mode.upper(), side, pnl_usd, pnl_pct * 100, self.balance,
        )

        self.trade_history.append({**position, "type": "CLOSE"})
        return {"status": "CLOSED", "pnl_usd": pnl_usd, "balance": self.balance}

    async def adjust_position(self, decision: dict, snapshot: dict) -> dict:
        """Adjust stop loss or take profit on existing position"""
        # TODO: Implement trailing stop, partial close
        return {"status": "NOT_IMPLEMENTED"}

    async def close_all_positions(self) -> dict:
        """Emergency close all positions"""
        closed = 0
        for pos in list(self.positions):
            # Use last known price
            await self._close_position(pos["entry_price"], {})
            closed += 1

        logger.warning("🚨 Emergency close: %d positions closed", closed)
        return {"status": "ALL_CLOSED", "count": closed}
