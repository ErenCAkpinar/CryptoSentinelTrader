# Track Record — transparent, risk-first reporting layer

A **read-only** product surface on top of the live BreakoutBot. It parses the
bot's own append-only log + state into a single `track_record.json`, which a
static web page and a Telegram channel consume.

**It never trades and never imports the trading engine.** It only *reads*
`/root/BreakoutBot/{paper_bb.log, state_paper.json, config.py}`. The validated
bot stays untouched.

## Why it looks the way it does

The moat is **verifiable transparency + risk discipline**, not secret alpha. So:

- The dashboard foregrounds **max drawdown, fixed-dollar risk per trade, and
  circuit-breaker events** — the "doesn't blow up" story — not a hero return number.
- The web page is **static** and reads a public JSON. There is no backend that
  could fabricate numbers; every value is reconstructable from the raw log
  (`journalctl -u breakoutbot`).
- Every surface carries the honest disclaimers: **testnet (not real money),
  no return promises, not financial advice, no exotic-indicator claims.**

## Components

| File | Role |
|------|------|
| `models.py` | `track_record.json` schema (pydantic). The contract between all parts. |
| `exporter.py` | Parses log + state + config → `track_record.json`. Read-only. |
| `web/` | Static dashboard (`index.html`, `styles.css`, `app.js`). Zero dependencies. |
| `telegram_broadcaster.py` | Posts new trades/risk-events to a public channel (HTTP, idempotent). |
| `deploy/` | systemd `service`+`timer`, nginx config, env example. |
| `sample/` | Fixture log/state/config for local testing. |

## Local development

```bash
# From repo root, using the repo venv (has pydantic):
.venv/bin/python -m track_record.exporter \
  --log track_record/sample/paper_bb.log \
  --state track_record/sample/state_paper.json \
  --config track_record/sample/config.py \
  --out track_record/web/track_record.json

# Serve the dashboard:
.venv/bin/python -m http.server 8137 --directory track_record/web
#   → http://localhost:8137  (override data with ?data=<url>)

# Preview the Telegram feed without sending:
.venv/bin/python -m track_record.telegram_broadcaster \
  --data track_record/web/track_record.json --dry-run --backfill 10
```

`track_record/web/track_record.json` is gitignored — it is generated, not source.

## Optional: live backtest cards

The dashboard's backtest cards default to the documented Faz 4c numbers. To
populate them from a real run, export a card and feed it to the exporter:

```bash
.venv/bin/python python/sentinel/data/backtest_engine.py \
  --symbol INJUSDT --days 90 --json /tmp/bt.json --period-label "INJ 90d"
.venv/bin/python -m track_record.exporter ... --backtest-json /tmp/bt.json --out ...
```

## Server deployment (Hetzner)

The live bot already runs at `/root/BreakoutBot`. Deploy this layer alongside it.

```bash
# 1. Put the package on the server (parent dir must be on sys.path).
mkdir -p /root/track-record-app
scp -r track_record root@157.180.117.112:/root/track-record-app/   # → /root/track-record-app/track_record/

# 2. One-package venv (exporter needs only pydantic; broadcaster is stdlib-only).
python3 -m venv /root/track-record-app/.venv
/root/track-record-app/.venv/bin/pip install pydantic

# 3. Web root + static assets.
mkdir -p /var/www/track-record
cp -r /root/track-record-app/track_record/web/index.html \
      /root/track-record-app/track_record/web/styles.css \
      /root/track-record-app/track_record/web/app.js /var/www/track-record/

# 4. Telegram (optional but it's the free distribution channel).
cp /root/track-record-app/track_record/deploy/telegram.env.example /root/track-record-app/telegram.env
nano /root/track-record-app/telegram.env        # fill token + channel id

# 5. systemd timer (export + broadcast every 10 min).
cp /root/track-record-app/track_record/deploy/track-record.service /etc/systemd/system/
cp /root/track-record-app/track_record/deploy/track-record.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now track-record.timer
systemctl start track-record.service            # first run: seeds Telegram cursor, writes JSON

# 6. nginx static site + HTTPS.
cp /root/track-record-app/track_record/deploy/nginx-track-record.conf /etc/nginx/sites-available/track-record
#   edit server_name, then:
ln -s /etc/nginx/sites-available/track-record /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx -d track.example.com
```

### Verify the deploy

```bash
# JSON freshly written and reconciles with the raw log:
cat /var/www/track-record/track_record.json | head
journalctl -u breakoutbot | grep "CLOSE FULL" | tail   # cross-check trades/balance
systemctl list-timers track-record.timer               # next run scheduled
curl -s https://track.example.com/track_record.json | head
```

If the Telegram channel isn't configured yet, comment out the second `ExecStart`
in `track-record.service` — the dashboard still updates on its own.

## What this is NOT (yet)

Phase 2 (separate work): the paid product — a Whop/Telegram subscription that
fuses live regime + Kelly-sized entries + the risk dashboard ($25–50/mo). This
layer is the free ~30-day trust builder that measures conversion first. No
payment or gating code lives here.
