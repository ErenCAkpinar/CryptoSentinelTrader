# Minimal sample mirroring the live bot's risk constants (for local exporter tests).
# The real file is /root/BreakoutBot/config.py on the server.

FULL_SIZE_USD = 300.0
LEVERAGE = 3
RISK_PER_TRADE_USD = 10.0
MIN_NOTIONAL_USD = 300.0
MAX_NOTIONAL_USD = 1500.0

MR_ENABLED = True
EQUITY_THROTTLE_DD = -0.07
PEAK_DD_LIMIT = -0.15
DAILY_DD_LIMIT = -0.05
DAILY_SL_LIMIT = 2
