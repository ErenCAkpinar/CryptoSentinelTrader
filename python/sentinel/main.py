"""
CryptoSentinelTrader — Main Pipeline (Sentinel Architecture)
Receives market snapshots from Rust core engine via ZMQ,
processes through AI decision ensemble, and executes trades.
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, UTC
from pathlib import Path

# Add project root and python directory to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / "python"))
sys.path.insert(0, str(project_root))

from sentinel.ai_engine.ensemble import DecisionEngine
from sentinel.risk.executor import TradeExecutor
from sentinel.data.config_loader import load_config
from sentinel.risk.kill_switch import KillSwitch
from sentinel.risk.circuit_breaker import CircuitBreaker
from sentinel.strategies.scanner import OpportunityScanner

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("data/events.log"),
        logging.StreamHandler()
    ]
)
# Silence noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING); logging.getLogger("httpcore").setLevel(logging.WARNING); logging.getLogger("urllib3").setLevel(logging.WARNING); logging.getLogger("ccxt.base.exchange").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)

logger = logging.getLogger("sentinel.pipeline")


class RustCoreManager:
    """Manage the Rust core engine as a subprocess."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.process: subprocess.Popen | None = None
        self._current_symbols: list[str] = []

    def start(self, symbols: list[str] | None = None):
        """Start (or restart) the Rust core engine."""
        self.stop()

        # Updated path for workspace root target
        core_binary = self.project_root / "target" / "release" / "sentinel-ingestion"
        core_dir = self.project_root / "rust" / "sentinel-ingestion"

        if not core_binary.exists():
            # Try debug build
            core_binary = self.project_root / "target" / "debug" / "sentinel-ingestion"

        if not core_binary.exists():
            logger.warning(
                "Rust binary not found. Build with: cd rust/sentinel-ingestion && cargo build --release"
            )
            return

        if symbols:
            self._current_symbols = symbols

        try:
            self.process = subprocess.Popen(
                [str(core_binary)],
                cwd=str(core_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            logger.info(
                "Rust core started (PID: %d) | symbols: %s",
                self.process.pid,
                self._current_symbols,
            )

            # Start thread to pipe Rust logs to Python logger
            import threading
            def pipe_logs(pipe):
                for line in iter(pipe.readline, ''):
                    clean_line = line.strip()
                    if clean_line:
                        print(f"\033[94m[Rust]\033[0m {clean_line}")
                        logger.info(f"[RustCore] {clean_line}")
                pipe.close()
            
            threading.Thread(target=pipe_logs, args=(self.process.stdout,), daemon=True).start()

        except Exception as e:
            logger.error("Failed to start Rust core: %s", e)

    def stop(self):
        """Stop the Rust core engine subprocess."""
        if self.process and self.process.poll() is None:
            logger.info("Stopping Rust core (PID: %d)...", self.process.pid)
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            logger.info("Rust core stopped")
        self.process = None

    def restart_if_symbols_changed(self, new_symbols: list[str]) -> bool:
        old = set(self._current_symbols)
        new = set(new_symbols)
        diff_count = len(old.symmetric_difference(new))
        if diff_count >= 1:
            logger.info("Symbol list changed — restarting Rust core")
            self.start(new_symbols)
            return True
        return False

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None


class SentinelPipeline:
    """Main orchestration pipeline: Snapshot -> Decision -> Execution"""

    def __init__(self, config: dict, mode: str = "paper"):
        self.config = config
        self.mode = mode
        self.running = False
        self.decision_engine = DecisionEngine(config)
        self.executor = TradeExecutor(config, mode=mode)
        self.circuit_breaker = CircuitBreaker(config)
        self.scanner = OpportunityScanner(config)
        self.rust_manager = RustCoreManager(Path(__file__).parent.parent.parent)
        self.stats = {
            "snapshots_processed": 0,
            "decisions_made": 0,
            "trades_executed": 0,
            "errors": 0,
            "start_time": None,
        }
        self._last_prices: dict[str, float] = {}

    async def start(self):
        """Start the pipeline."""
        self.running = True
        self.stats["start_time"] = datetime.now(UTC).isoformat()
        logger.info("🛡️ CryptoSentinel Pipeline starting in %s mode", self.mode)

        # Start Rust core subprocess
        initial_symbols = self.config.get("exchange", {}).get("symbols", ["BTCUSDT", "ETHUSDT"])
        self.rust_manager.start(initial_symbols)

        # Build task list
        tasks = [
            self._run_data_loop(),
            self.executor.run_watchdog(interval_sec=5),
        ]

        # Add scanner if enabled
        if self.config.get("scanner", {}).get("enabled", True):
            tasks.append(self._run_scanner_loop())

        try:
            await asyncio.gather(*tasks)
        finally:
            self.rust_manager.stop()

    async def _run_scanner_loop(self):
        """Periodically scan for new opportunities and update the pipeline."""
        logger.info("Scanner loop started")
        
        # Initial scan to populate symbols
        try:
            result = await self.scanner.scan_once()
            if result:
                new_symbols = result.get("active_symbols", [])
                if new_symbols:
                    self.scanner._update_config_toml(new_symbols)
                    self.rust_manager.restart_if_symbols_changed(new_symbols)
        except Exception as e:
            logger.error(f"Initial scan failed: {e}")

        while self.running:
            try:
                # Wait for the configured interval
                interval = self.config.get("scanner", {}).get("scan_interval_sec", 300)
                await asyncio.sleep(interval)
                
                logger.info("Running scheduled opportunity scan...")
                result = await self.scanner.scan_once()
                
                if result:
                    new_symbols = result.get("active_symbols", [])
                    if new_symbols:
                        self.scanner._update_config_toml(new_symbols)
                        # Restart Rust core if list changed significantly
                        if self.rust_manager.restart_if_symbols_changed(new_symbols):
                            logger.info(f"Updated active symbols: {new_symbols}")
            except Exception as e:
                logger.error(f"Scanner loop error: {e}")

    async def _run_data_loop(self):
        """Main data loop: receive snapshots from Rust core."""
        try:
            import zmq
            import zmq.asyncio

            ctx = zmq.asyncio.Context()
            socket = ctx.socket(zmq.SUB)
            socket.connect(self.config["pipeline"]["zmq_address"])
            socket.setsockopt_string(zmq.SUBSCRIBE, "")

            logger.info("Connected to Rust core at %s", self.config["pipeline"]["zmq_address"])

            while self.running:
                # Security Gate: Kill Switch
                if KillSwitch.is_active():
                    logger.critical("🚨 Kill Switch Active! Haling pipeline.")
                    await self.stop()
                    break

                try:
                    raw = await asyncio.wait_for(socket.recv_string(), timeout=30.0)
                    snapshot = json.loads(raw)
                    # Debug log to see EVERY snapshot that arrives
                    logger.debug("Received snapshot for %s", snapshot.get("symbol"))
                    asyncio.create_task(self.process_snapshot(snapshot))

                except asyncio.TimeoutError:
                    if not self.rust_manager.is_running():
                        logger.warning("Rust core not running — restarting...")
                        self.rust_manager.start()
                except Exception as e:
                    logger.error(f"Loop error: {e}")
                    self.stats["errors"] += 1

        except ImportError:
            logger.error("ZMQ not available.")

    async def process_snapshot(self, snapshot: dict):
        """Process a single market snapshot."""
        self.stats["snapshots_processed"] += 1
        symbol = snapshot.get("symbol", "")
        current_price = snapshot.get("price", {}).get("current", 0)
        self._last_prices[symbol] = current_price

        # 1. Circuit Breaker Check
        if not self.circuit_breaker.can_trade():
            return

        # 2. Decision Ensemble (Layer 3)
        decision = await self.decision_engine.analyze(snapshot)
        self.stats["decisions_made"] += 1

        action = decision.get("decision", {}).get("action", "HOLD")
        confidence = decision["decision"]["confidence"]
        
        # 3. Execution (Layer 4)
        if action != "HOLD":
            logger.info(f"🚨 ACTION: {action} for {symbol} | Confidence: {confidence}")
            await self.executor.execute(decision, snapshot)
            self.stats["trades_executed"] += 1
        else:
            logger.info(f"📊 HOLD for {symbol} | Consensus: {confidence}")

    async def stop(self):
        """Graceful shutdown."""
        logger.info("Shutting down pipeline...")
        self.running = False
        self.rust_manager.stop()
        await self.executor.close_all_positions()
        logger.info("Pipeline stopped.")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="CryptoSentinelTrader Pipeline")
    parser.add_argument("--mode", choices=["paper", "live"], default="paper")
    parser.add_argument("--config", default="config/config.toml")
    args = parser.parse_args()

    config = load_config(args.config)
    pipeline = SentinelPipeline(config, mode=args.mode)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(pipeline.stop()))

    await pipeline.start()

if __name__ == "__main__":
    asyncio.run(main())
