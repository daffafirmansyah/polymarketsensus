"""
Terminal Dashboard — exact format from original spec.
ALL data from real-time APIs (CLOB, Binance WS).
"""
import time
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

console = Console()


def fmt_time(ts: float = None) -> str:
    return datetime.fromtimestamp(ts or time.time()).strftime("%H:%M:%S")


def build_log_line(snap: MarketSnapshot, decision: dict, is_consensus: bool) -> str:
    """[HEARTBEAT] format — real-time CLOB data."""
    now = fmt_time()
    remaining = snap.seconds_remaining

    total_ob = snap.yes_ob_orders + snap.no_ob_orders
    total_vol = snap.total_ob_vol

    yes_w_pct = (snap.yes_ob_orders / total_ob * 100) if total_ob > 0 else 0
    no_w_pct = (snap.no_ob_orders / total_ob * 100) if total_ob > 0 else 0
    yes_v_pct = (snap.yes_ob_vol / total_vol * 100) if total_vol > 0 else 0
    no_v_pct = (snap.no_ob_vol / total_vol * 100) if total_vol > 0 else 0

    status = "No consensus"
    if is_consensus:
        status = f"🎯 CONSENSUS: {snap.consensus_side}"

    return (
        f"[{now}] [HEARTBEAT] T-{remaining}s | "
        f"Wallets: {snap.total_wallets} | "
        f"YES: {snap.yes_ob_orders} ({yes_w_pct:.0f}%) "
        f"Vol: ${snap.yes_ob_vol:,.0f} ({yes_v_pct:.0f}% / W:{yes_w_pct:.0f}%) "
        f"@ {snap.yes_price:.2f} | "
        f"NO: {snap.no_ob_orders} ({no_w_pct:.0f}%) "
        f"Vol: ${snap.no_ob_vol:,.0f} ({no_v_pct:.0f}% / W:{no_w_pct:.0f}%) "
        f"@ {snap.no_price:.2f} | "
        f"{status}"
    )


def build_status_panel(snap: MarketSnapshot) -> Panel:
    pos = trade_strategy.position
    try:
        balance = api_client.get_usdc_balance()
    except Exception:
        balance = 0.0
    pnl = trade_strategy.total_pnl
    pnl_color = "green" if pnl >= 0 else "red"
    w, l = trade_strategy.total_wins, trade_strategy.total_losses

    lines = [
        f"₿ BTC: ${snap.btc_price:,.0f}",
        f"💵 USDC: ${balance:,.2f}",
        f"📈 P&L: [bold {pnl_color}]${pnl:+.3f}[/] | W/L: [green]{w}[/green]/[red]{l}[/red]",
    ]
    if pos.is_open and pos.entry_price > 0:
        cp = snap.yes_price if pos.entry_side == "YES" else snap.no_price
        u = (cp - pos.entry_price) * pos.size
        u_color = "green" if u >= 0 else "red"
        lines += [
            "",
            f"[bold]🔴 {pos.entry_side}[/bold]",
            f"  {pos.size:.0f} @ {pos.entry_price:.3f} → {cp:.3f}",
            f"  Unrealized: [bold {u_color}]${u:+.3f}[/bold {u_color}]",
        ]
    else:
        lines += ["", "⚪ No position"]
    mode = "[yellow]DRY RUN[/yellow]" if bot_config.DRY_RUN else "[red]LIVE[/red]"
    lines += ["", f"Mode: {mode}"]
    return Panel("\n".join(lines), title="📊 Dashboard", border_style="cyan")


def build_consensus_panel(snap: MarketSnapshot) -> Panel:
    pos = trade_strategy.position
    if pos.is_open and pos.entry_price > 0:
        cp = snap.yes_price if pos.entry_side == "YES" else snap.no_price
        tp_hit = cp >= strategy_config.TP_PRICE
        tp_tag = "green" if tp_hit else "dim white"
        sl_risk = snap.consensus_side and snap.consensus_side != pos.entry_side
        sl_tag = "red" if sl_risk else "dim white"
        content = (
            f"[bold]🔴 {pos.entry_side}[/bold]\n"
            f"Entry: {pos.entry_price:.3f} → {cp:.3f}\n\n"
            f"[{tp_tag}]TP: {strategy_config.TP_PRICE*100:.0f}¢ {'✅' if tp_hit else ''}[/{tp_tag}]\n"
            f"[{sl_tag}]SL: Dynamic flip {'⚠️' if sl_risk else ''}[/{sl_tag}]\n\n"
            f"T-{snap.seconds_remaining}s"
        )
        return Panel(content, title="🎯 Exit Monitor", border_style="bold yellow")
    if snap.consensus_side:
        c = "green" if snap.consensus_side == "YES" else "red"
        return Panel(
            f"Side: [bold {c}]{snap.consensus_side}[/]\n"
            f"Wallet: {snap.consensus_wallet_pct:.1f}%\n"
            f"Volume: {snap.consensus_volume_pct:.1f}%\n"
            f"T-{snap.seconds_remaining}s\n"
            f"Price: {snap.yes_price if snap.consensus_side=='YES' else snap.no_price:.3f}",
            title="🎯 CONSENSUS!", border_style="bold green"
        )
    return Panel(
        f"Wallets: {snap.total_wallets}\nT-{snap.seconds_remaining}s",
        title="🎯 Awaiting Consensus", border_style="dim white"
    )


def build_market_panel(snap: MarketSnapshot) -> Panel:
    start_str = datetime.fromtimestamp(snap.market_start_ts).strftime("%H:%M:%S") if snap.market_start_ts else "?"
    end_str = datetime.fromtimestamp(snap.market_end_ts).strftime("%H:%M:%S") if snap.market_end_ts else "?"
    remaining = snap.seconds_remaining
    cd = f"T-{remaining//60}m{remaining%60}s" if remaining > 60 else f"T-{remaining}s"
    cc = "bold red" if remaining <= 10 else ("red" if remaining <= 30 else ("yellow" if remaining <= 60 else "green"))
    blink = "🔴" if remaining <= 10 else ("🟡" if remaining <= 30 else "🟢")
    yc, nc = ("green", "white") if snap.yes_price > snap.no_price else ("white", "red")

    age = time.time() - snap.last_fetched
    live_dot = "🟢" if age < 2 else ("🟡" if age < 5 else "🔴")
    fetched_str = datetime.fromtimestamp(snap.last_fetched).strftime("%H:%M:%S")

    slug_dt = ""
    m = re.search(r'btc-updown-5m-(\d+)', snap.slug)
    if m:
        slug_dt = datetime.fromtimestamp(int(m.group(1))).strftime("%b %d, %H:%M")

    return Panel(
        f"🕐 {start_str} → {end_str} UTC\n"
        f"{'📛' if remaining <= 10 else '⏳'} [{cc}]{cd}[/]\n"
        f"{blink}{slug_dt}\n{snap.slug}\n{live_dot} {fetched_str}\n\n"
        f"[bold {yc}]YES: {snap.yes_price:.3f}[/]  [bold {nc}]NO: {snap.no_price:.3f}[/]\n"
        f"Bid: {snap.best_bid:.2f}/{snap.no_best_bid:.2f}  Ask: {snap.best_ask:.2f}/{snap.no_best_ask:.2f}\n\n"
        f"OB Vol: ${snap.total_ob_vol:,.0f} (Y:${snap.yes_ob_vol:,.0f}/N:${snap.no_ob_vol:,.0f})\n"
        f"Holders: {snap.total_wallets} (Y:{snap.yes_orders}/N:{snap.no_orders})",
        title=f"📡 {snap.question}", border_style="bold magenta"
    )


def build_strategy_panel() -> Panel:
    cfg = strategy_config
    return Panel(
        "\n".join([
            f"MIN_WALLETS: {cfg.MIN_WALLETS}",
            f"MIN_CONSENSUS: {cfg.MIN_CONSENSUS}%",
            f"TP: {cfg.TP_PRICE*100:.0f}¢",
            f"MAX_ENTRY: {cfg.MAX_ENTRY*100:.0f}¢",
            f"Entry: T-{cfg.ENTRY_WINDOW_START}s to T-{cfg.ENTRY_WINDOW_END}s",
            f"Max Trade: ${cfg.MAX_TRADE:.2f} | Min: ${cfg.MIN_TRADE:.2f}",
            f"Max Position: {cfg.MAX_POSITION}",
        ]),
        title="⚙️ Strategy", border_style="dim cyan"
    )


class Dashboard:
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
        layout.split(Layout(name="top", size=12), Layout(name="log", size=16), Layout(name="spacer"))
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
                Layout(Panel("...", title="📡")),
                Layout(Panel("...", title="📊")),
                Layout(Panel("...", title="🎯")),
                Layout(build_strategy_panel()),
            )
        layout["top"].update(top)
        visible = self.log_lines[-16:] if len(self.log_lines) > 16 else self.log_lines
        log_text = "\n".join(visible) if visible else "Waiting..."
        layout["log"].update(Panel(Text(log_text, style="dim"), title="📜 Trade Log", border_style="blue"))

        # Spacer = mini status bar
        snap = self.last_snap
        status_bar = f"₿ ${snap.btc_price:,.0f}" if snap and snap.btc_price > 0 else ""
        pos = trade_strategy.position
        if pos.is_open and snap:
            cp = snap.yes_price if pos.entry_side == "YES" else snap.no_price
            u = (cp - pos.entry_price) * pos.size
            status_bar += f"  |  🔴 {pos.entry_side} {pos.size:.0f}sh @ {pos.entry_price:.3f} → {cp:.3f} [{'green' if u >= 0 else 'red'}]${u:+.3f}[/]"
        status_bar += f"  |  P&L: [{'green' if trade_strategy.total_pnl >= 0 else 'red'}]${trade_strategy.total_pnl:+.3f}[/]"
        status_bar += f"  |  W/L: {trade_strategy.total_wins}/{trade_strategy.total_losses}"
        status_bar += f"  |  {'[yellow]DRY[/yellow]' if bot_config.DRY_RUN else '[red]LIVE[/red]'}"
        layout["spacer"].update(Panel(Text(status_bar), border_style="dim"))
        return layout

    def update(self, snap: MarketSnapshot, decision: dict):
        self.last_snap = snap
        is_consensus = bool(snap.consensus_side)

        # 1. Heartbeat line
        self.add_log(build_log_line(snap, decision, is_consensus))

        # 2. Consensus event
        if is_consensus:
            self.add_log("🎯 CONSENSUS REACHED!")
            self.add_log(f"[PRICE] {snap.slug}: {snap.consensus_side} @ {snap.yes_price if snap.consensus_side == 'YES' else snap.no_price:.2f}")

        # 3. Trade events
        if decision["action"] == "ENTER":
            self.add_log(f"[TRADE] BUY {decision['side']} {decision['size']:.2f} USDC @ {decision['price']:.2f}")
            self.add_log(f"[TRADE] FILLED {decision['side']} {decision['size']:.0f} tokens @ {decision['price']:.2f} (cost ${decision['price'] * decision['size']:.3f})")
        elif decision["action"] in ("EXIT_TP", "EXIT_SL"):
            label = "TAKE PROFIT" if decision["action"] == "EXIT_TP" else "STOP LOSS"
            self.add_log(f"[TRADE] {label} — SELL {decision['size']:.0f} @ {decision['price']:.2f}")

    def start(self):
        self.live = Live(self.render(), console=console, refresh_per_second=4, screen=True)
        self.live.__enter__()

    def stop(self):
        if self.live:
            self.live.__exit__(None, None, None)

    def refresh(self):
        if self.live:
            self.live.update(self.render())


dashboard = Dashboard()
