"""
CryptoSentinelTrader — Main Pipeline
Receives market snapshots from Rust core engine via ZMQ,
processes through AI decision engine, and executes trades.
"""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_engine.decision_engine import DecisionEngine
from execution.executor import TradeExecutor
from config.settings import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sentinel.pipeline")


class SentinelPipeline:
    """Main orchestration pipeline: Snapshot → Decision → Execution"""

    def __init__(self, config: dict, mode: str = "paper"):
        self.config = config
        self.mode = mode
        self.running = False
        self.decision_engine = DecisionEngine(config)
        self.executor = TradeExecutor(config, mode=mode)
        self.stats = {
            "snapshots_processed": 0,
            "decisions_made": 0,
            "trades_executed": 0,
            "errors": 0,
            "start_time": None,
        }

    async def start(self):
        """Start the pipeline — connect to Rust core via ZMQ"""
        self.running = True
        self.stats["start_time"] = datetime.utcnow().isoformat()
        logger.info("🛡️ CryptoSentinel Pipeline starting in %s mode", self.mode)

        try:
            import zmq
            import zmq.asyncio

            ctx = zmq.asyncio.Context()
            socket = ctx.socket(zmq.SUB)
            socket.connect(self.config["pipeline"]["zmq_address"])
            socket.setsockopt_string(zmq.SUBSCRIBE, "")  # Subscribe to all

            logger.info(
                "📡 Connected to Rust core at %s",
                self.config["pipeline"]["zmq_address"],
            )

            while self.running:
                try:
                    # Receive snapshot JSON from Rust core
                    raw = await asyncio.wait_for(socket.recv_string(), timeout=30.0)
                    snapshot = json.loads(raw)
                    await self.process_snapshot(snapshot)

                except asyncio.TimeoutError:
                    logger.warning("No snapshot received for 30s — is core engine running?")
                except json.JSONDecodeError as e:
                    logger.error("Invalid JSON from core: %s", e)
                    self.stats["errors"] += 1

        except ImportError:
            logger.warning("ZMQ not available — falling back to file-based IPC")
            await self._file_based_loop()

    async def _file_based_loop(self):
        """Fallback: read snapshots from a shared JSON file (dev/testing)"""
        snapshot_path = Path(self.config["pipeline"].get("snapshot_file", "/tmp/sentinel_snapshot.json"))
        logger.info("📂 File-based mode: watching %s", snapshot_path)

        while self.running:
            if snapshot_path.exists():
                try:
                    snapshot = json.loads(snapshot_path.read_text())
                    await self.process_snapshot(snapshot)
                except Exception as e:
                    logger.error("Error reading snapshot file: %s", e)
                    self.stats["errors"] += 1

            await asyncio.sleep(self.config["signals"]["snapshot_interval_sec"])

    async def process_snapshot(self, snapshot: dict):
        """Core processing loop: Snapshot → Decision → Execution"""
        self.stats["snapshots_processed"] += 1
        snapshot_id = snapshot.get("meta", {}).get("snapshot_id", "unknown")

        # Log basic info
        price = snapshot.get("price", {})
        volume = snapshot.get("volume", {})
        anomaly = snapshot.get("anomaly_detection", {})

        logger.info(
            "📊 %s | %s @ $%.2f | Vol: %.1fx | Anomaly: %s",
            snapshot_id,
            snapshot.get("symbol", "?"),
            price.get("current", 0),
            volume.get("volume_ratio", 1),
            "⚠️ YES" if anomaly.get("is_anomaly") else "no",
        )

        # Step 1: AI Decision Engine
        decision = await self.decision_engine.analyze(snapshot)
        self.stats["decisions_made"] += 1

        action = decision.get("decision", {}).get("action", "HOLD")
        confidence = decision.get("consensus", {}).get("weighted_confidence", 0)

        logger.info(
            "🧠 Decision: %s (confidence: %.2f) | Risk tier: %s",
            action,
            confidence,
            decision.get("risk_parameters", {}).get("confidence_tier", "?"),
        )

        # Step 2: Execute if action required
        if action in ("ENTER_LONG", "ENTER_SHORT", "EXIT", "STOP"):
            result = await self.executor.execute(decision, snapshot)
            self.stats["trades_executed"] += 1
            logger.info("⚡ Execution: %s", result)
        elif action == "ADJUST":
            result = await self.executor.adjust_position(decision, snapshot)
            logger.info("🔧 Position adjusted: %s", result)

        # Step 3: Log stats periodically
        if self.stats["snapshots_processed"] % 100 == 0:
            self._log_stats()

    def _log_stats(self):
        """Log pipeline statistics"""
        logger.info(
            "📈 Stats | Snapshots: %d | Decisions: %d | Trades: %d | Errors: %d",
            self.stats["snapshots_processed"],
            self.stats["decisions_made"],
            self.stats["trades_executed"],
            self.stats["errors"],
        )

    async def stop(self):
        """Graceful shutdown"""
        logger.info("🛡️ Shutting down pipeline...")
        self.running = False
        await self.executor.close_all_positions()
        self._log_stats()
        logger.info("🛡️ Pipeline stopped.")


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="CryptoSentinelTrader Pipeline")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument("--config", default="config/config.toml")
    args = parser.parse_args()

    config = load_config(args.config)
    pipeline = SentinelPipeline(config, mode=args.mode)

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(pipeline.stop()))

    await pipeline.start()


if __name__ == "__main__":
    asyncio.run(main())
