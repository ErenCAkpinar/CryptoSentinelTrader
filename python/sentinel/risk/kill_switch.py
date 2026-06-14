"""
Kill Switch Module — Emergency shutdown of all trading activities.
Triggers immediate closing of all open positions and blocks new entries.
"""

import logging
import os
import json
from pathlib import Path

logger = logging.getLogger("sentinel.risk.kill_switch")

KILL_FILE = Path("data/kill_switch.lock")

class KillSwitch:
    @staticmethod
    def activate():
        """Activate the kill switch by creating a lock file."""
        logger.warning("🚨 KILL SWITCH ACTIVATED! Creating lock file.")
        KILL_FILE.parent.mkdir(parents=True, exist_ok=True)
        KILL_FILE.touch()

    @staticmethod
    def deactivate():
        """Deactivate the kill switch by removing the lock file."""
        if KILL_FILE.exists():
            logger.info("✅ Kill switch deactivated.")
            KILL_FILE.unlink()

    @staticmethod
    def is_active() -> bool:
        """Check if the kill switch is currently active."""
        return KILL_FILE.exists()

    @staticmethod
    async def emergency_shutdown(executor):
        """Close all positions via the provided executor."""
        logger.critical("执行紧急停机 (Emergency Shutdown)...")
        await executor.close_all_positions()
        KillSwitch.activate()
