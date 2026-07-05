"""Transparent track-record reporting layer (read-only) for the live BreakoutBot.

This package never trades and never imports the trading engine. It parses the
bot's own append-only log + state into `track_record.json`, which a static web
page and a Telegram broadcaster consume. See README.md for deployment.
"""

from .models import TrackRecord, SCHEMA_VERSION

__all__ = ["TrackRecord", "SCHEMA_VERSION"]
