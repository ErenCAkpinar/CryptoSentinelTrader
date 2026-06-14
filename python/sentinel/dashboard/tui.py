"""
Terminal TUI for CryptoSentinel — Powered by Textual
Provides a real-time, Bloomberg-style dashboard for monitoring.
"""

import sys
from pathlib import Path

# Add project root and python directory to path
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root / "python"))
sys.path.insert(0, str(project_root))

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static
from textual.containers import Grid
from textual.binding import Binding
import json

# Paths to bot data
POSITIONS_FILE = Path("data/positions.json")
WHALES_FILE = Path("data/whale_alerts.json")
EVENTS_FILE = Path("data/events.log")

class DashboardWidget(Static):
    """Base widget with a border and a title."""
    def __init__(self, title: str, **kwargs):
        super().__init__(**kwargs)
        self.border_title = title
        # Create an ID for the inner content based on the title
        self.content_id = f"{title.lower().replace(' ', '-')}-content"

    def compose(self) -> ComposeResult:
        # Yield the actual content static inside the widget
        yield Static("", id=self.content_id)

class SentinelTUI(App):
    """The main Sentinel TUI application."""
    
    CSS = """
    Screen {
        background: #0d0d0d;
    }
    Header {
        background: #1a1a1a;
        color: #00ff00;
        border-bottom: solid #333;
    }
    DashboardWidget {
        border: round #333;
        padding: 0 1;
        margin: 0;
        height: 100%;
        background: #0d0d0d;
    }
    DashboardWidget:focus {
        border: round #00ffff;
    }
    #pnl-header {
        height: 3;
        background: #1a1a1a;
        border-bottom: solid #333;
        content-align: center middle;
    }
    #main-grid {
        grid-size: 3 2;
        grid-gutter: 1 2;
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("k", "kill", "Emergency Kill Switch", priority=True),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Initializing Sentinel Core...", id="pnl-header")
        with Grid(id="main-grid"):
            yield DashboardWidget("Whale Stream", id="whales", classes="widget")
            yield DashboardWidget("Positions", id="positions", classes="widget")
            yield DashboardWidget("Strategy Stats", id="stats", classes="widget")
            yield DashboardWidget("Signal Feed", id="signals", classes="widget")
            yield DashboardWidget("Risk Status", id="risk", classes="widget")
            yield DashboardWidget("System Logs", id="logs", classes="widget")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(1.0, self.update_data)

    def update_data(self) -> None:
        try:
            if POSITIONS_FILE.exists():
                with open(POSITIONS_FILE, "r") as f:
                    data = json.load(f)
                    balance = data.get("balance", 0)
                    daily_pnl = data.get("daily_pnl", 0)
                    pnl_color = "green" if daily_pnl >= 0 else "red"
                    self.query_one("#pnl-header").update(
                        f"Balance: [bold]$ {balance:,.2f}[/] | Daily PnL: [bold {pnl_color}]$ {daily_pnl:+.2f}[/]"
                    )

            pos_content = self.query_one("#positions-content")
            if POSITIONS_FILE.exists():
                with open(POSITIONS_FILE, "r") as f:
                    data = json.load(f)
                    positions = data.get("positions", [])
                    if not positions:
                        pos_content.update("[dim]No active positions...[/]")
                    else:
                        rows = [f"[{'green' if p['side'] == 'LONG' else 'red'}]{p['side']:<5}[/] {p['symbol']:<10} @ {p['entry_price']}" for p in positions]
                        pos_content.update("\n".join(rows))

            # Update System Logs from events.log
            logs_content = ""
            signals_content = ""
            if EVENTS_FILE.exists():
                try:
                    with open(EVENTS_FILE, "r") as f:
                        lines = f.readlines()
                        # Last 15 lines for System Logs
                        recent_logs = [line.strip()[-100:] for line in lines[-15:]]
                        logs_content = "\n".join(recent_logs)
                        
                        # Filter lines for Signal Feed (looking for AI decisions)
                        signal_lines = [line.strip() for line in lines if ">>> " in line or "score=" in line]
                        signals_content = "\n".join([line[-80:] for line in signal_lines[-10:]])
                except Exception as e:
                    logs_content = f"[red]Error reading logs: {e}[/]"
            else:
                logs_content = "[dim]Waiting for events.log...[/]"
                signals_content = "[dim]Waiting for AI signals...[/]"

            self.query_one("#system-logs-content").update(logs_content)
            self.query_one("#signal-feed-content").update(signals_content)

            self.query_one("#whale-stream-content").update("🐋 Monitoring Polymarket Leaderboard...\n🔥 No convergence alerts.")
            self.query_one("#risk-status-content").update("Circuit Breaker: [bold green]OK[/]\nDaily Loss Limit: 10%\nMode: [bold white]PAPER[/]")
            self.query_one("#strategy-stats-content").update("HMM Regime: [bold cyan]SCANNING...[/]\nWin Rate: --%\nTrades: 0")

        except Exception as e:
            if hasattr(self, "query_one"):
                try:
                    self.query_one("#system-logs-content").update(f"[red]TUI Error: {e}[/]")
                except:
                    pass

    def action_kill(self) -> None:
        from sentinel.risk.kill_switch import KillSwitch
        KillSwitch.activate()
        try:
            self.query_one("#system-logs-content").update("[bold red]🚨 KILL SWITCH ACTIVATED[/]")
        except:
            pass

if __name__ == "__main__":
    app = SentinelTUI()
    app.run()
