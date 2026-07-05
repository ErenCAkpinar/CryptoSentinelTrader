"""
Telegram broadcaster — the free, public transparency channel.

Reads track_record.json (produced by exporter.py) and posts any *new* closed
trade or risk-circuit event to a public Telegram channel. This is the free
trust-building feed, NOT a paid signal channel: it announces results after the
fact, never an actionable "buy now" call.

Dependency-free: posts via the Telegram Bot HTTP API with urllib (same approach
as the live bot's TestnetOrderManager), so no extra packages are required.

Idempotency: a small cursor file remembers the last broadcast timestamps, so
re-running (e.g. from a systemd timer) never double-posts. First run seeds the
cursor silently — use --backfill N to post the most recent N events on day one.

Usage:
    export TELEGRAM_BOT_TOKEN=123:abc
    export TELEGRAM_CHANNEL_ID=@my_public_channel   # or -100… numeric id
    python -m track_record.telegram_broadcaster --data track_record.json
    python -m track_record.telegram_broadcaster --data track_record.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

_EXIT_EMOJI = {"TP1": "✅", "TP2": "✅", "TRAIL": "✅", "SL": "❌",
               "MR_TP": "✅", "MR_SL": "❌", "MR_TIMEOUT": "⏱️"}
_EVENT_TEXT = {
    "HARD_STOP": "🛑 Hard-stop guard fired — system paused, no new risk taken.",
    "THROTTLE_ON": "🔻 Drawdown −7% → position size automatically halved.",
    "THROTTLE_OFF": "🔺 Drawdown recovered → position size back to full.",
    "DAILY_FREEZE": "⚠️ Daily stop-loss limit hit on a symbol → frozen for the day.",
}


def _send(token: str, chat_id: str, text: str, dry_run: bool) -> None:
    if dry_run:
        print("— would send —\n" + text + "\n")
        return
    data = urllib.parse.urlencode({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(TELEGRAM_API.format(token=token), data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read())
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API error: {body}")


def _fmt_trade(t: dict, max_dd_pct: float | None, mode: str) -> str:
    emoji = _EXIT_EMOJI.get(t["exit_type"], "•")
    pnl = t["pnl"]
    pnl_s = f"{'+' if pnl >= 0 else '−'}${abs(pnl):.2f}"
    regime = t.get("regime_at_entry") or "—"
    risk = f"risk ${t['risk_usd']:.0f}" if t.get("risk_usd") is not None else "MR sleeve"
    dd = f" · maxDD {max_dd_pct*100:.1f}%" if max_dd_pct is not None else ""
    return (f"{emoji} <b>{t['symbol']}</b> close [{t['exit_type']}] {pnl_s}\n"
            f"regime {regime} · {risk} · bal ${t['balance_after']:.2f}{dd}\n"
            f"<i>{mode.lower()} · results only, not a signal</i>")


def _fmt_event(e: dict, mode: str) -> str:
    base = _EVENT_TEXT.get(e["type"], e.get("detail", e["type"]))
    return f"{base}\n<i>{mode.lower()} · risk guardrail working as designed</i>"


def _load_cursor(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"last_trade_ts": None, "last_event_ts": None}


def _after(ts: str | None, cursor: str | None) -> bool:
    """True if ts is strictly newer than cursor (cursor None ⇒ everything is new)."""
    if ts is None:
        return False
    if cursor is None:
        return True
    return datetime.fromisoformat(ts) > datetime.fromisoformat(cursor)


def broadcast(data_path: Path, cursor_path: Path, token: str, chat_id: str,
              dry_run: bool, backfill: int) -> int:
    doc = json.loads(data_path.read_text(encoding="utf-8"))
    mode = doc["meta"].get("mode", "TESTNET")
    max_dd = doc["risk_summary"].get("max_dd_pct")
    trades = sorted(doc.get("trades", []), key=lambda t: t["ts_close"])
    events = sorted(doc.get("risk_events", []), key=lambda e: e["ts"])

    cursor = _load_cursor(cursor_path)
    first_run = cursor["last_trade_ts"] is None and cursor["last_event_ts"] is None

    if first_run and backfill <= 0:
        # Seed silently so we don't dump the whole history into the channel.
        cursor = {
            "last_trade_ts": trades[-1]["ts_close"] if trades else None,
            "last_event_ts": events[-1]["ts"] if events else None,
        }
        if not dry_run:
            cursor_path.write_text(json.dumps(cursor, indent=2), encoding="utf-8")
        print(f"{'(dry-run) ' if dry_run else ''}Seeded cursor (no posts). "
              f"{len(trades)} trades / {len(events)} events known.")
        return 0

    if first_run and backfill > 0:
        new_trades = trades[-backfill:]
        new_events = events[-backfill:]
    else:
        new_trades = [t for t in trades if _after(t["ts_close"], cursor["last_trade_ts"])]
        new_events = [e for e in events if _after(e["ts"], cursor["last_event_ts"])]

    sent = 0
    # Interleave by time so the feed reads chronologically.
    merged = ([("trade", t, t["ts_close"]) for t in new_trades]
              + [("event", e, e["ts"]) for e in new_events])
    for kind, item, _ts in sorted(merged, key=lambda x: x[2]):
        text = _fmt_trade(item, max_dd, mode) if kind == "trade" else _fmt_event(item, mode)
        _send(token, chat_id, text, dry_run)
        sent += 1

    if not dry_run:
        cursor = {
            "last_trade_ts": trades[-1]["ts_close"] if trades else cursor["last_trade_ts"],
            "last_event_ts": events[-1]["ts"] if events else cursor["last_event_ts"],
        }
        cursor_path.write_text(json.dumps(cursor, indent=2), encoding="utf-8")
    print(f"{'(dry-run) ' if dry_run else ''}Broadcast {sent} item(s).")
    return sent


def main() -> None:
    ap = argparse.ArgumentParser(description="Broadcast new track-record events to a public Telegram channel.")
    ap.add_argument("--data", type=Path, default=Path("track_record.json"))
    ap.add_argument("--cursor", type=Path, default=Path("track_record/.broadcast_cursor.json"))
    ap.add_argument("--dry-run", action="store_true", help="Print messages instead of sending.")
    ap.add_argument("--backfill", type=int, default=0, help="On first run, post the most recent N events.")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID", "")
    if not args.dry_run and (not token or not chat_id):
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID env vars (or use --dry-run).")

    broadcast(args.data, args.cursor, token, chat_id, args.dry_run, args.backfill)


if __name__ == "__main__":
    main()
