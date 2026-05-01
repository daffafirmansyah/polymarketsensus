"""
Terminal Dashboard — live display of BTC price, balance, P&L, and trade log.
Uses Rich for pretty terminal output.
ALL data from real-time APIs (CLOB, holders, Binance) — no stale Gamma midpoint.
"""
import time
import logging
import re
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.layout import Layout
from rich.text import Text

from config import bot_config, strategy_config
from polymarket_api import client as api_client, MarketSnapshot
from strategy import strategy as trade_strategy

logger = logging.getLogger("polybot.dashboard")
console = Console()


def fmt_time(ts: float = None) -> str:
    t = ts or time.time()
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


def build_log_line(snap: MarketSnapshot, decision: dict, is_consensus: bool) -> str:
    """Log line: CLOB real-time orders + volumes + holders wallets."""
    now = fmt_time()
    remaining = snap.seconds_remaining

    # Market timestamp from slug
    slug_time = ""
    m = re.search(r'btc-updown-5m-(\d+)', snap.slug)
    if m:
        slug_time = datetime.fromtimestamp(int(m.group(1))).strftime("%H:%M")

    # Real-time CLOB orderbook orders
    total_ob_orders = snap.yes_ob_orders + snap.no_ob_orders
    yes_w_pct = (snap.yes_ob_orders / total_ob_orders * 100) if total_ob_orders > 0 else 0
    no_w_pct = (snap.no_ob_orders / total_ob_orders * 100) if total_ob_orders > 0 else 0

    # Real-time CLOB orderbook volume
    yes_v_pct = (snap.yes_ob_vol / snap.total_ob_vol * 100) if snap.total_ob_vol > 0 else 0
    no_v_pct = (snap.no_ob_vol / snap.total_ob_vol * 100) if snap.total_ob_vol > 0 else 0

    status = "No consensus"
    if is_consensus:
        status = f"🎯 CONSENSUS: {snap.consensus_side} "

    line = (
        f"[{now}] [{slug_time}] T-{remaining}s | "
        f"OB: {snap.yes_ob_orders + snap.no_ob_orders} | Holders: {snap.total_wallets} | "
        f"YES: {snap.yes_ob_orders} ({yes_w_pct:.0f}%) "
        f"Vol: ${snap.yes_ob_vol:,.0f} ({yes_v_pct:.0f}% / W:{yes_w_pct:.0f}%) "
        f"@ {snap.yes_price:.3f} | "
        f"NO: {snap.no_ob_orders} ({no_w_pct:.0f}%) "
        f"Vol: ${snap.no_ob_vol:,.0f} ({no_v_pct:.0f}% / W:{no_w_pct:.0f}%) "
        f"@ {snap.no_price:.3f} | "
        f"{status}"
    )
    return line


def build_status_panel(snap: MarketSnapshot) -> Panel:
    """Dashboard panel — safe for open positions."""
    pos = trade_strategy.position

    try:
        balance = api_client.get_usdc_balance()
    except Exception:
        balance = 0.0

    pnl = trade_strategy.total_pnl
    pnl_color = "green" if pnl >= 0 else "red"
    w = trade_strategy.total_wins
    l = trade_strategy.total_losses

    lines = [
        f"₿ BTC: ${snap.btc_price:,.0f}",
        f"💵 USDC: ${balance:,.2f}",
        f"📈 P&L: [bold {pnl_color}]${pnl:+.3f}[/] | W/L: [green]{w}[/green]/[red]{l}[/red]",
    ]

    if pos.is_open and pos.entry_price > 0:
        current_price = snap.yes_price if pos.entry_side == "YES" else snap.no_price
        unrealized = (current_price - pos.entry_price) * pos.size
        u_color = "green" if unrealized >= 0 else "red"
        lines += [
            "",
            "[bold]🔴 OPEN POSITION[/bold]",
            f"  {pos.entry_side} {pos.size:.0f} shares @ {pos.entry_price:.3f}",
            f"  Now: {current_price:.3f} | P&L: [bold {u_color}]${unrealized:+.3f}[/]",
        ]
    else:
        lines += ["", "⚪ No position"]

    lines += [
        "",
        f"Mode: [yellow]DRY RUN[/yellow]" if bot_config.DRY_RUN else "Mode: [red]LIVE[/red]",
    ]

    return Panel("\n".join(lines), title="📊 Dashboard", border_style="cyan")


def build_consensus_panel(snap: MarketSnapshot) -> Panel:
    """Exit Monitor (when in position) or Consensus (when flat)."""
    pos = trade_strategy.position

    if pos.is_open and pos.entry_price > 0:
        current_price = snap.yes_price if pos.entry_side == "YES" else snap.no_price
        tp_hit = current_price >= strategy_config.TP_PRICE
        tp_color = "green" if tp_hit else "dim"
        sl_color = "red" if snap.consensus_side and snap.consensus_side != pos.entry_side else "dim"
        content = (
            f"[bold]🔴 Position: {pos.entry_side}[/bold]\n"
            f"Entry: {pos.entry_price:.3f}\n"
            f"Current: {current_price:.3f}\n\n"
            f"[{tp_color}]TP: {strategy_config.TP_PRICE*100:.0f}¢ {'✅' if tp_hit else ''}[/{tp_color}]\n"
            f"[{sl_color}]SL: Dynamic (flip >75%)[/{sl_color}]\n\n"
            f"T-{snap.seconds_remaining}s remaining"
        )
        return Panel(content, title="🎯 Exit Monitor", border_style="bold yellow")

    if snap.consensus_side:
        side = snap.consensus_side
        color = "green" if side == "YES" else "red"
        content = (
            f"Side: [bold {color}]{side}[/bold {color}]\n"
            f"Wallet: {snap.consensus_wallet_pct:.1f}%\n"
            f"Volume: {snap.consensus_volume_pct:.1f}%\n"
            f"T-{snap.seconds_remaining}s\n"
            f"Price: {snap.yes_price if side == 'YES' else snap.no_price:.3f}"
        )
        return Panel(content, title="🎯 CONSENSUS!", border_style="bold green")

    return Panel(
        f"Wallets: {snap.total_wallets}\nT-{snap.seconds_remaining}s",
        title="🎯 Awaiting Consensus",
        border_style="dim white"
    )


def build_market_panel(snap: MarketSnapshot) -> Panel:
    """Live market panel — all CLOB real-time data."""
    start_str = datetime.fromtimestamp(snap.market_start_ts).strftime("%H:%M:%S") if snap.market_start_ts else "?"
    end_str = datetime.fromtimestamp(snap.market_end_ts).strftime("%H:%M:%S") if snap.market_end_ts else "?"

    remaining = snap.seconds_remaining
    if remaining > 60:
        countdown = f"T-{remaining // 60}m {remaining % 60}s"
    else:
        countdown = f"T-{remaining}s"

    if remaining <= 10:
        countdown_color = "bold red"
        blink = "🔴 "
    elif remaining <= 30:
        countdown_color = "red"
        blink = "🟡 "
    elif remaining <= 60:
        countdown_color = "yellow"
        blink = "🟢 "
    else:
        countdown_color = "green"
        blink = "🟢 "

    yes_color = "green" if snap.yes_price > snap.no_price else "white"
    no_color = "red" if snap.no_price > snap.yes_price else "white"

    # Market timestamp from slug
    slug_dt = ""
    m = re.search(r'btc-updown-5m-(\d+)', snap.slug)
    if m:
        slug_dt = datetime.fromtimestamp(int(m.group(1))).strftime("%b %d, %H:%M")

    # Data freshness
    age = time.time() - snap.last_fetched
    live_dot = "🟢" if age < 2 else ("🟡" if age < 5 else "🔴")
    fetched_str = datetime.fromtimestamp(snap.last_fetched).strftime("%H:%M:%S")

    content = (
        f"🕐 {start_str} → {end_str} UTC\n"
        f"{'📛' if remaining <= 10 else '⏳'} [{countdown_color}]{countdown}[/{countdown_color}]\n"
        f"{blink}{slug_dt}\n"
        f"{snap.slug}\n"
        f"{live_dot} Updated: {fetched_str}\n\n"
        f"[bold {yes_color}]YES: {snap.yes_price:.3f}[/]  "
        f"[bold {no_color}]NO: {snap.no_price:.3f}[/]\n"
        f"Bid: {snap.best_bid:.2f}  Ask: {snap.best_ask:.2f}  "
        f"(NO Bid: {snap.no_best_bid:.2f}  Ask: {snap.no_best_ask:.2f})\n\n"
        f"OB Vol: ${snap.total_ob_vol:,.0f} (YES:${snap.yes_ob_vol:,.0f} / NO:${snap.no_ob_vol:,.0f})\n"
        f"OB Orders: {snap.yes_ob_orders} YES / {snap.no_ob_orders} NO\n"
        f"Wallets: {snap.total_wallets}"
    )

    return Panel(content, title=f"📡 {snap.question}", border_style="bold magenta")


def build_strategy_panel() -> Panel:
    """Strategy parameters panel."""
    cfg = strategy_config
    lines = [
        f"MIN_WALLETS: {cfg.MIN_WALLETS}",
        f"MIN_CONSENSUS: {cfg.MIN_CONSENSUS}%",
        f"TP: {cfg.TP_PRICE*100:.0f}¢",
        f"MAX_ENTRY: {cfg.MAX_ENTRY*100:.0f}¢",
        f"Entry: T-{cfg.ENTRY_WINDOW_START}s to T-{cfg.ENTRY_WINDOW_END}s",
        f"Max Trade: {cfg.MAX_TRADE} shares",
        f"Min Trade: {cfg.MIN_TRADE} shares",
        f"Max Position: {cfg.MAX_POSITION}",
    ]
    return Panel("\n".join(lines), title="⚙️ Strategy", border_style="dim cyan")


class Dashboard:
    """Rich live dashboard — 1s refresh, all real-time data."""

    def __init__(self):
        self.log_lines: list[str] = []
        self.max_log_lines = 100
        self.visible_log_lines = 30
        self.live: Optional[Live] = None
        self.last_snap: Optional[MarketSnapshot] = None

    def add_log(self, line: str):
        self.log_lines.append(line)
        if len(self.log_lines) > self.max_log_lines:
            self.log_lines = self.log_lines[-self.max_log_lines:]

    def render(self) -> Layout:
        layout = Layout()
        layout.split(Layout(name="top", size=12), Layout(name="bottom"))

        top = Layout()
        if self.last_snap:
            top.split_row(
                Layout(build_market_panel(self.last_snap), ratio=2),
                Layout(build_status_panel(self.last_snap), ratio=2),
                Layout(build_consensus_panel(self.last_snap), ratio=1),
                Layout(build_strategy_panel(), ratio=1),
            )
        else:
            top.split_row(
                Layout(Panel("Loading...", title="📡 Market")),
                Layout(Panel("Loading...", title="📊 Dashboard")),
                Layout(Panel("Loading...", title="🎯 Consensus")),
                Layout(build_strategy_panel()),
            )

        layout["top"].update(top)

        visible = self.log_lines[-self.visible_log_lines:] if len(self.log_lines) > self.visible_log_lines else self.log_lines
        log_text = "\n".join(visible) if visible else "Waiting for first scan..."
        layout["bottom"].update(Panel(Text(log_text, style="dim"), title="📜 Trade Log", border_style="blue"))

        return layout

    def update(self, snap: MarketSnapshot, decision: dict):
        self.last_snap = snap

        is_consensus = bool(snap.consensus_side)
        log_line = build_log_line(snap, decision, is_consensus)
        self.add_log(log_line)

        if decision["action"] == "ENTER":
            self.add_log(f"[TRADE] ▶ BUY {decision['side']} {decision['size']:.0f} @ {decision['price']:.3f}")
        elif decision["action"] == "EXIT_TP":
            self.add_log(f"[TRADE] 🏆 TAKE PROFIT — SELL {decision['size']:.0f} @ {decision['price']:.3f}")
        elif decision["action"] == "EXIT_SL":
            self.add_log(f"[TRADE] 🛑 STOP LOSS — SELL {decision['size']:.0f} @ {decision['price']:.3f}")
        elif is_consensus and trade_strategy.position.state.value == "flat":
            self.add_log(f"🎯 CONSENSUS: {snap.consensus_side} (W:{snap.consensus_wallet_pct:.0f}% V:{snap.consensus_volume_pct:.0f}%)")

    def start(self):
        self.live = Live(self.render(), console=console, refresh_per_second=4, screen=True)
        self.live.__enter__()

    def stop(self):
        if self.live:
            self.live.__exit__(None, None, None)
            self.live = None

    def refresh(self):
        if self.live:
            self.live.update(self.render())


dashboard = Dashboard()
