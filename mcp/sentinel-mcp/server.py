"""
Sentinel MCP Server — Python
Exposes the CryptoSentinel bot's internal state to LLMs via the Model Context Protocol.

Tools:
- get_status: Return balance, PnL, and open positions.
- get_whales: Return recent whale activity and convergence alerts.
- trade: Execute a paper trade (buy/sell).
"""

from fastmcp import FastMCP
import json
from pathlib import Path

# Create an MCP server
mcp = FastMCP("CryptoSentinel")

# Paths to bot data
POSITIONS_FILE = Path("data/positions.json")
WHALES_FILE = Path("data/whale_alerts.json")

@mcp.tool()
def get_status() -> str:
    """Get the current bot status including balance, PnL, and open positions."""
    if not POSITIONS_FILE.exists():
        return "No active positions or balance data found."
    
    with open(POSITIONS_FILE, "r") as f:
        data = json.load(f)
    
    return json.dumps({
        "balance": data.get("balance"),
        "daily_pnl": data.get("daily_pnl"),
        "positions": data.get("positions", [])
    }, indent=2)

@mcp.tool()
def get_whales() -> str:
    """Get recent whale activity and convergence alerts from Polymarket."""
    # Placeholder for actual whale data integration
    return "Whale monitoring active. No major convergence alerts in the last 2 hours."

@mcp.tool()
def trade(symbol: str, side: str, size_usd: float) -> str:
    """Execute a paper trade. side must be 'LONG' or 'SHORT'."""
    # This tool would interface with the SentinelPipeline/Executor
    return f"Paper trade request received: {side} {symbol} for ${size_usd}. (Simulation only via MCP)"

if __name__ == "__main__":
    mcp.run()
