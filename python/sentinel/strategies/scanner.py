"""
Crypto Opportunity Scanner — Scans all Binance Futures USDT pairs,
scores them with MathEngine, and updates config.toml with the best
trading opportunities.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("sentinel.scanner")

# Add project root to path for imports
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_engine.decision_engine import MathEngine


class OpportunityScanner:
    """Periodically scans all Binance Futures USDT pairs and updates active symbols."""

    def __init__(self, config: dict):
        self.config = config
        self.scanner_config = config.get("scanner", {})
        self.math_engine = MathEngine()
        self.running = False
        self.exchange = None
        self._last_symbols: list[str] = []

        self.scan_interval = self.scanner_config.get("scan_interval_sec", 300)
        self.min_volume = self.scanner_config.get("min_volume_24h_usdt", 10_000_000)
        self.min_score = self.scanner_config.get("min_score", 55)
        self.max_symbols = self.scanner_config.get("max_symbols", 10)
        self.ohlcv_timeframe = self.scanner_config.get("ohlcv_timeframe", "15m")
        self.ohlcv_limit = self.scanner_config.get("ohlcv_limit", 100)
        self.top_candidates = self.scanner_config.get("top_candidates_for_scoring", 30)
        self.always_include = self.scanner_config.get("always_include", ["BTCUSDT", "ETHUSDT"])
        self.excluded = set(self.scanner_config.get("excluded_symbols", []))

        self._init_exchange()

    def _init_exchange(self):
        """Initialize ccxt Binance Futures (public endpoints only, no auth needed)."""
        try:
            import ccxt

            exchange_id = self.config.get("exchange", {}).get("primary", "binance")
            is_sandbox = "testnet" in exchange_id

            self.exchange = ccxt.binance({
                "options": {"defaultType": "future"},
                "enableRateLimit": True,
                "sandbox": is_sandbox,
            })
            self.exchange.load_markets()
            logger.info(
                "Scanner exchange initialized: binance futures (sandbox: %s) — %d markets",
                is_sandbox,
                len(self.exchange.markets),
            )
        except Exception as e:
            logger.error("Scanner exchange init failed: %s", e)

    async def start(self) -> None:
        """Run the scan loop. Returns new symbols list when it changes."""
        self.running = True
        logger.info(
            "Scanner started | interval: %ds | min_volume: $%s | min_score: %s | max_symbols: %d",
            self.scan_interval,
            f"{self.min_volume:,.0f}",
            self.min_score,
            self.max_symbols,
        )

        while self.running:
            try:
                result = await self.scan_once()
                if result:
                    new_symbols = result.get("active_symbols", [])
                    if new_symbols and set(new_symbols) != set(self._last_symbols):
                        logger.info(
                            "Symbol list changed: %s -> %s",
                            self._last_symbols,
                            new_symbols,
                        )
                        self._update_config_toml(new_symbols)
                        self._last_symbols = new_symbols
            except Exception as e:
                logger.error("Scan cycle failed: %s", e)

            await asyncio.sleep(self.scan_interval)

    async def scan_once(self) -> Optional[dict]:
        """Execute a single scan cycle: fetch → filter → score → rank → output."""
        if not self.exchange:
            logger.warning("Exchange not initialized, skipping scan")
            return None

        t0 = time.time()

        # Step 1: Fetch all tickers (single REST call)
        try:
            tickers = await asyncio.get_event_loop().run_in_executor(
                None, self.exchange.fetch_tickers
            )
        except Exception as e:
            logger.error("fetch_tickers failed: %s", e)
            return None

        # Step 2: Filter to USDT linear futures with sufficient volume
        candidates = self._filter_pairs(tickers)
        total_scanned = len(tickers)
        logger.info(
            "Scanned %d pairs → %d candidates after volume filter ($%s min)",
            total_scanned,
            len(candidates),
            f"{self.min_volume:,.0f}",
        )

        if not candidates:
            return None

        # Step 3: Fetch OHLCV and score each candidate
        scored = []
        for candidate in candidates[: self.top_candidates]:
            try:
                result = await self._fetch_and_score(candidate)
                if result:
                    scored.append(result)
            except Exception as e:
                logger.warning("Failed to score %s: %s", candidate["symbol"], e)
            # Small delay to respect rate limits
            await asyncio.sleep(0.05)

        # Step 4: Rank by composite score, filter by min_score
        scored.sort(key=lambda x: x["composite_score"], reverse=True)
        qualified = [s for s in scored if s["composite_score"] >= self.min_score]

        # Step 5: Build active symbols list (always_include + top opportunities)
        active_symbols = list(self.always_include)
        for item in qualified:
            sym = item["symbol"]
            if sym not in active_symbols and len(active_symbols) < self.max_symbols:
                active_symbols.append(sym)

        scan_duration_ms = int((time.time() - t0) * 1000)

        # Log results
        logger.info(
            "Scan complete in %dms | %d scored | %d qualified (score >= %d) | %d active",
            scan_duration_ms,
            len(scored),
            len(qualified),
            self.min_score,
            len(active_symbols),
        )
        for i, item in enumerate(qualified[:15], 1):
            logger.info(
                "  %2d. %-10s score: %5.1f  %-12s  vol: %5.1fx  RSI: %4.1f  trend: %s",
                i,
                item["symbol"],
                item["composite_score"],
                item["signal"],
                item.get("volume_ratio", 1.0),
                item.get("rsi_14", 50),
                item.get("trend", "?"),
            )

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scan_duration_ms": scan_duration_ms,
            "total_pairs_scanned": total_scanned,
            "pairs_after_volume_filter": len(candidates),
            "pairs_scored": len(scored),
            "pairs_qualified": len(qualified),
            "recommended_symbols": qualified[:self.max_symbols],
            "active_symbols": active_symbols,
        }

        return result

    def _filter_pairs(self, tickers: dict) -> list[dict]:
        """Filter to USDT linear futures with sufficient volume, exclude stablecoins."""
        candidates = []

        for symbol_key, ticker in tickers.items():
            # ccxt symbol format: "BTC/USDT:USDT"
            if not symbol_key.endswith(":USDT"):
                continue

            # Get the raw symbol (e.g., "BTCUSDT")
            info = ticker.get("info", {})
            raw_symbol = info.get("symbol", "")
            if not raw_symbol:
                # Derive from ccxt symbol
                raw_symbol = symbol_key.replace("/", "").replace(":USDT", "")

            if raw_symbol in self.excluded:
                continue

            quote_volume = ticker.get("quoteVolume") or 0
            if quote_volume < self.min_volume:
                continue

            candidates.append({
                "symbol": raw_symbol,
                "ccxt_symbol": symbol_key,
                "price": ticker.get("last") or 0,
                "quote_volume_24h": quote_volume,
                "change_pct": ticker.get("percentage") or 0,
            })

        # Sort by 24h volume descending
        candidates.sort(key=lambda x: x["quote_volume_24h"], reverse=True)
        return candidates

    async def _fetch_and_score(self, candidate: dict) -> Optional[dict]:
        """Fetch OHLCV for a symbol, compute indicators, score with MathEngine."""
        symbol = candidate["ccxt_symbol"]
        raw_symbol = candidate["symbol"]

        try:
            ohlcv = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self.exchange.fetch_ohlcv(
                    symbol, self.ohlcv_timeframe, limit=self.ohlcv_limit
                ),
            )
        except Exception as e:
            logger.warning("[%s] OHLCV fetch failed: %s", raw_symbol, e)
            return None

        if not ohlcv or len(ohlcv) < 30:
            return None

        # Build snapshot from OHLCV
        snapshot = self._ohlcv_to_snapshot(raw_symbol, ohlcv, candidate)

        # Score with MathEngine
        math_result = self.math_engine.analyze(snapshot)

        return {
            "symbol": raw_symbol,
            "ccxt_symbol": symbol,
            "composite_score": math_result["composite_score"],
            "signal": math_result["signal"],
            "kelly_fraction": math_result["kelly_fraction"],
            "price": candidate["price"],
            "volume_24h_usdt": candidate["quote_volume_24h"],
            "volume_ratio": snapshot["volume"]["volume_ratio"],
            "rsi_14": snapshot["indicators"]["rsi_14"],
            "macd_histogram": snapshot["indicators"]["macd_histogram"],
            "trend": snapshot["price"]["trend"],
            "trend_strength": snapshot["price"]["trend_strength"],
            "ema_alignment": snapshot["indicators"]["ema_alignment"],
        }

    def _ohlcv_to_snapshot(self, symbol: str, ohlcv: list, candidate: dict) -> dict:
        """Convert OHLCV candles to a snapshot dict matching MathEngine input format."""
        # ohlcv format: [[timestamp, open, high, low, close, volume], ...]
        closes = np.array([c[4] for c in ohlcv], dtype=float)
        highs = np.array([c[2] for c in ohlcv], dtype=float)
        lows = np.array([c[3] for c in ohlcv], dtype=float)
        opens = np.array([c[1] for c in ohlcv], dtype=float)
        volumes = np.array([c[5] for c in ohlcv], dtype=float)

        current_price = closes[-1]

        # RSI(14)
        rsi = self._calc_rsi(closes, 14)

        # EMA(9, 21, 50)
        ema_9 = self._calc_ema(closes, 9)
        ema_21 = self._calc_ema(closes, 21)
        ema_50 = self._calc_ema(closes, 50)

        if ema_9 > ema_21 > ema_50:
            ema_alignment = "BULLISH"
        elif ema_9 < ema_21 < ema_50:
            ema_alignment = "BEARISH"
        else:
            ema_alignment = "MIXED"

        # MACD(12, 26, 9)
        macd_line = self._calc_ema(closes, 12) - self._calc_ema(closes, 26)
        # For signal line, compute full MACD series then EMA of it
        macd_series = self._calc_ema_series(closes, 12) - self._calc_ema_series(closes, 26)
        signal_line = self._calc_ema(macd_series, 9)
        macd_histogram = macd_line - signal_line

        # Bollinger Bands(20, 2)
        if len(closes) >= 20:
            bb_sma = np.mean(closes[-20:])
            bb_std = np.std(closes[-20:], ddof=0)
            bb_upper = bb_sma + 2 * bb_std
            bb_lower = bb_sma - 2 * bb_std
            bb_range = bb_upper - bb_lower
            bb_position_pct = ((current_price - bb_lower) / bb_range * 100) if bb_range > 0 else 50
        else:
            bb_position_pct = 50

        # ATR(14)
        atr = self._calc_atr(highs, lows, closes, 14)

        # VWAP (session approximation from available candles)
        typical_prices = (highs + lows + closes) / 3
        cum_tpv = np.cumsum(typical_prices * volumes)
        cum_vol = np.cumsum(volumes)
        vwap = cum_tpv[-1] / cum_vol[-1] if cum_vol[-1] > 0 else current_price

        # Volume ratio: last candle vs 20-period average
        if len(volumes) >= 20:
            avg_vol = np.mean(volumes[-20:])
            vol_ratio = volumes[-1] / avg_vol if avg_vol > 0 else 1.0
        else:
            vol_ratio = 1.0

        # Buy/sell ratio approximation: proportion of bullish candles (close > open)
        recent = min(20, len(closes))
        bullish = np.sum(closes[-recent:] > opens[-recent:])
        buy_sell_ratio = bullish / recent

        # Trend from recent price action
        if len(closes) >= 5:
            pct_change = (closes[-1] - closes[-5]) / closes[-5] * 100
        else:
            pct_change = 0

        if pct_change > 0.1:
            trend = "UP"
        elif pct_change < -0.1:
            trend = "DOWN"
        else:
            trend = "SIDEWAYS"
        trend_strength = min(abs(pct_change) / 1.0, 1.0)

        return {
            "symbol": symbol,
            "price": {
                "current": current_price,
                "trend": trend,
                "trend_strength": trend_strength,
            },
            "volume": {
                "volume_ratio": vol_ratio,
                "buy_sell_ratio": buy_sell_ratio,
                "is_spike": vol_ratio > 2.0,
            },
            "indicators": {
                "rsi_14": rsi,
                "rsi_signal": "OVERBOUGHT" if rsi > 70 else "OVERSOLD" if rsi < 30 else "NEUTRAL",
                "ema_9": ema_9,
                "ema_21": ema_21,
                "ema_50": ema_50,
                "ema_alignment": ema_alignment,
                "macd_value": macd_line,
                "macd_signal": signal_line,
                "macd_histogram": macd_histogram,
                "bb_position_pct": bb_position_pct,
                "atr_14": atr,
                "vwap": vwap,
            },
            "anomaly_detection": {
                "is_anomaly": False,
                "anomaly_score": 0.0,
            },
        }

    # ── Indicator helpers ────────────────────────────────────────────────────

    @staticmethod
    def _calc_rsi(closes: np.ndarray, period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = np.diff(closes[-(period + 1):])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _calc_ema(data: np.ndarray, period: int) -> float:
        if len(data) == 0:
            return 0.0
        if len(data) < period:
            return float(np.mean(data))
        multiplier = 2.0 / (period + 1.0)
        ema = np.mean(data[:period])
        for val in data[period:]:
            ema = (val - ema) * multiplier + ema
        return float(ema)

    @staticmethod
    def _calc_ema_series(data: np.ndarray, period: int) -> np.ndarray:
        """Return full EMA series (same length as input)."""
        if len(data) == 0:
            return data
        result = np.empty_like(data)
        if len(data) < period:
            result[:] = np.mean(data)
            return result
        multiplier = 2.0 / (period + 1.0)
        result[:period] = np.mean(data[:period])
        ema = result[period - 1]
        for i in range(period, len(data)):
            ema = (data[i] - ema) * multiplier + ema
            result[i] = ema
        return result

    @staticmethod
    def _calc_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
        if len(closes) < 2:
            return float(closes[-1] * 0.01) if len(closes) > 0 else 0.0
        tr_hl = highs[1:] - lows[1:]
        tr_hc = np.abs(highs[1:] - closes[:-1])
        tr_lc = np.abs(lows[1:] - closes[:-1])
        tr = np.maximum(tr_hl, np.maximum(tr_hc, tr_lc))
        if len(tr) < period:
            return float(np.mean(tr))
        return float(np.mean(tr[-period:]))

    # ── Config update ────────────────────────────────────────────────────────

    def _update_config_toml(self, new_symbols: list[str]) -> None:
        """Update the symbols list in config.toml. Preserves all other settings."""
        # Fix: Look for config/config.toml in the project root
        config_path = Path(__file__).parent.parent.parent.parent / "config" / "config.toml"

        if not config_path.exists():
            logger.warning("config.toml not found at %s, cannot update symbols", config_path)
            return

        try:
            content = config_path.read_text()

            # Find and replace the symbols line under [exchange]
            import re

            symbols_str = json.dumps(new_symbols)
            # Match: symbols = ["...", "..."]  with possible whitespace/comments
            new_content = re.sub(
                r'^(\s*symbols\s*=\s*)(\[.*?\])',
                rf'\g<1>{symbols_str}',
                content,
                count=1,
                flags=re.MULTILINE,
            )

            if new_content == content:
                logger.warning("Could not find symbols line in config.toml to update")
                return

            # Atomic write: write to temp then rename
            tmp_path = config_path.with_suffix(".toml.tmp")
            tmp_path.write_text(new_content)
            tmp_path.rename(config_path)

            logger.info("Updated config.toml symbols: %s", new_symbols)
        except Exception as e:
            logger.error("Failed to update config.toml: %s", e)

    def get_active_symbols(self) -> list[str]:
        """Return the last discovered active symbols list."""
        return self._last_symbols if self._last_symbols else list(self.always_include)

    def stop(self):
        """Stop the scanner loop."""
        self.running = False
        logger.info("Scanner stopped")


# ── Standalone execution ─────────────────────────────────────────────────────

async def _main():
    """Run a single scan for testing."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%H:%M:%S",
    )

    from data.config_loader import load_config

    config = load_config()
    config["scanner"]["enabled"] = True

    scanner = OpportunityScanner(config)
    result = await scanner.scan_once()

    if result:
        print(f"\n{'='*60}")
        print(f"Active Symbols: {result['active_symbols']}")
        print(f"{'='*60}")
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(_main())
