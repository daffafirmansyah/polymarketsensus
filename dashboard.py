"""
Terminal Dashboard — live display of BTC price, balance, P&L, and trade log.
Uses Rich for pretty terminal output.
"""
import time
import logging
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text
from rich import box

from config import bot_config, strategy_config
from polymarket_api import client as api_client, MarketSnapshot
from strategy import strategy as trade_strategy

logger = logging.getLogger("polybot.dashboard")
console = Console()


def fmt_time(ts: float = None) -> str:
    """Format timestamp as HH:MM:SS."""
    t = ts or time.time()
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


def fmt_price(p: float) -> str:
    """Format price with color-coding."""
    if p >= 0.90:
        return f"[red]${p:.2f}[/red]"
    elif p >= 0.70:
        return f"[yellow]${p:.2f}[/yellow]"
    else:
        return f"[green]${p:.2f}[/green]"


def build_log_line(snap: MarketSnapshot, decision: dict, is_consensus: bool) -> str:
    """
    Build a single log line in the format:
    [HH:MM:SS] [HEARTBEAT] T-s | Wallets: N | YES: N (%) Vol: $X (V%/W:W%) @ P | NO: N (%) Vol: $X (V%/W:W%) @ P | status
    """
    now = fmt_time()
    remaining = snap.seconds_remaining

    total_orders = snap.yes_orders + snap.no_orders
    total_vol = snap.yes_volume + snap.no_volume

    yes_w_pct = (snap.yes_orders / total_orders * 100) if total_orders > 0 else 0
    no_w_pct = (snap.no_orders / total_orders * 100) if total_orders > 0 else 0
    yes_v_pct = (snap.yes_volume / total_vol * 100) if total_vol > 0 else 0
    no_v_pct = (snap.no_volume / total_vol * 100) if total_vol > 0 else 0

    status = "No consensus"
    if is_consensus:
        status = f"🎯 CONSENSUS: {snap.consensus_side} "

    line = (
        f"[{now}] [HEARTBEAT] T-{remaining}s | "
        f"Wallets: {snap.total_wallets} | "
        f"YES: {snap.yes_orders} ({yes_w_pct:.0f}%) "
        f"Vol: ${snap.yes_volume:.0f} ({yes_v_pct:.0f}% / W:{yes_w_pct:.0f}%) "
        f"@ {snap.yes_price:.2f} | "
        f"NO: {snap.no_orders} ({no_w_pct:.0f}%) "
        f"Vol: ${snap.no_volume:.0f} ({no_v_pct:.0f}% / W:{no_w_pct:.0f}%) "
        f"@ {snap.no_price:.2f} | "
        f"{status}"
    )
    return line


def build_status_panel(snap: MarketSnapshot) -> Panel:
    """Build the status/dashboard panel."""
    pos = trade_strategy.position

    # BTC price line
    btc_line = f"₿ BTC: ${snap.btc_price:,.0f}"

    # Balance
    try:
        balance = api_client.get_usdc_balance()
    except Exception:
        balance = 0.0
    bal_line = f"💵 USDC: ${balance:,.2f}"

    # P&L
    pnl = trade_strategy.total_pnl
    pnl_color = "green" if pnl >= 0 else "red"
    w = trade_strategy.total_wins
    l = trade_strategy.total_losses
    pnl_line = f"📈 P&L: [bold {pnl_color}]${pnl:+.3f}[/bold {pnl_color}] | W/L: [green]{w}[/green]/[red]{l}[/red]"

    # Position
    if pos.is_open:
        current_price = snap.yes_price if pos.entry_side == "YES" else snap.no_price
        unrealized = (current_price - pos.entry_price) * pos.size
        pos_line = (
            f"🔴 POSITION: {pos.entry_side} {pos.size} shares @ {pos.entry_price:.2f} | "
            f"Now: {current_price:.2f} | "
            f"Unrealized: [{'green' if unrealized >= 0 else 'red'}]${unrealized:+.3f}[/]"
        )
    else:
        pos_line = "⚪ No position"

    # Mode
    mode = "[yellow]DRY RUN[/yellow]" if bot_config.DRY_RUN else "[red]LIVE TRADING[/red]"

    content = "\n".join([btc_line, bal_line, pnl_line, pos_line, f"Mode: {mode}"])
    return Panel(content, title="📊 Dashboard", border_style="cyan")


def build_consensus_panel(snap: MarketSnapshot) -> Optional[Panel]:
    """Build consensus alert panel."""
    if not snap.consensus_side:
        return None

    side = snap.consensus_side
    color = "green" if side == "YES" else "red"
    content = (
        f"Side: [bold {color}]{side}[/bold {color}]\n"
        f"Wallet Consensus: {snap.consensus_wallet_pct:.1f}%\n"
        f"Volume Consensus: {snap.consensus_volume_pct:.1f}%\n"
        f"Time Remaining: T-{snap.seconds_remaining}s\n"
        f"Price: {snap.yes_price if side == 'YES' else snap.no_price:.2f}"
    )
    return Panel(content, title="🎯 CONSENSUS REACHED!", border_style="bold green")


def build_strategy_panel() -> Panel:
    """Build strategy parameters panel."""
    cfg = strategy_config
    lines = [
        f"MIN_WALLETS: {cfg.MIN_WALLETS}",
        f"MIN_CONSENSUS: {cfg.MIN_CONSENSUS}%",
        f"TP: {cfg.TP_PRICE:.0f}¢",
        f"MAX_ENTRY: {cfg.MAX_ENTRY:.0f}¢",
        f"Entry Window: T-{cfg.ENTRY_WINDOW_START}s to T-{cfg.ENTRY_WINDOW_END}s",
        f"Max Trade: {cfg.MAX_TRADE} shares",
        f"Min Trade: {cfg.MIN_TRADE} shares",
        f"Max Position: {cfg.MAX_POSITION}",
    ]
    return Panel("\n".join(lines), title="⚙️ Strategy", border_style="dim cyan")


class Dashboard:
    """Rich-based live dashboard."""

    def __init__(self):
        self.log_lines: list[str] = []
        self.max_log_lines = 20
        self.live: Optional[Live] = None
        self.last_snap: Optional[MarketSnapshot] = None

    def add_log(self, line: str):
        """Add a log line, keeping only recent ones."""
        self.log_lines.append(line)
        if len(self.log_lines) > self.max_log_lines:
            self.log_lines = self.log_lines[-self.max_log_lines:]

    def render(self) -> Layout:
        """Render the full dashboard layout."""
        layout = Layout()
        layout.split(
            Layout(name="top", size=10),
            Layout(name="bottom"),
        )

        # Top: status + consensus
        top = Layout()
        if self.last_snap:
            top.split_row(
                Layout(build_status_panel(self.last_snap), ratio=2),
                Layout(
                    build_consensus_panel(self.last_snap) or Panel("Waiting...", title="🎯 Consensus"),
                    ratio=1,
                ),
                Layout(build_strategy_panel(), ratio=1),
            )
        else:
            top_split = Layout()
            top_split.split_row(
                Layout(Panel("Loading...", title="📊 Dashboard")),
                Layout(Panel("Loading...", title="🎯 Consensus")),
                Layout(build_strategy_panel()),
            )
            top = top_split

        layout["top"].update(top)

        # Bottom: log
        log_text = "\n".join(self.log_lines) if self.log_lines else "Waiting for first scan..."
        log_panel = Panel(
            Text(log_text, style="dim"),
            title="📜 Trade Log",
            border_style="blue",
        )
        layout["bottom"].update(log_panel)

        return layout

    def update(self, snap: MarketSnapshot, decision: dict):
        """Update dashboard with new snapshot and decision."""
        self.last_snap = snap

        # Build log line
        is_consensus = bool(snap.consensus_side)
        log_line = build_log_line(snap, decision, is_consensus)
        self.add_log(log_line)

        # Handle trade events differently
        if is_consensus:
            self.add_log("🎯 CONSENSUS REACHED!")
            side = snap.consensus_side
            price = snap.yes_price if side == "YES" else snap.no_price
            self.add_log(f"[PRICE] {snap.slug}: {side} @ {price:.2f}")

        if decision["action"] == "ENTER":
            self.add_log(
                f"[TRADE] BUY {decision['side']} "
                f"{decision['size']:.2f} USDC @ {decision['price']:.2f}"
            )

    def start(self):
        """Start the live dashboard."""
        self.live = Live(self.render(), console=console, refresh_per_second=4, screen=True)
        self.live.__enter__()

    def stop(self):
        """Stop the live dashboard."""
        if self.live:
            self.live.__exit__(None, None, None)
            self.live = None

    def refresh(self):
        """Refresh the live display."""
        if self.live:
            self.live.update(self.render())


# Singleton
dashboard = Dashboard()
