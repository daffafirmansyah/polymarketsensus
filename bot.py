#!/usr/bin/env python3
"""
Polymarket BTC 5m Consensus Trading Bot
=======================================
Scans BTC up/down 5-minute markets on Polymarket,
uses consensus-based strategy for entries,
and executes trades (dry-run or real).

Usage:
    python bot.py              # Start bot (respects DRY_RUN in .env)
    python bot.py --live       # Force live trading mode
    python bot.py --dry        # Force dry run mode
    python bot.py --once       # Single scan + exit (for testing)
"""
import argparse
import logging
import signal
import sys
import time
from datetime import datetime

from config import bot_config, strategy_config
from polymarket_api import client as api_client, MarketSnapshot, TradeResult
from strategy import strategy as trade_strategy, Strategy
from dashboard import dashboard, console, fmt_time

# ── Logging Setup ─────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polybot.bot")

# ── Global Shutdown Flag ──────────────────
shutdown = False


def signal_handler(sig, frame):
    global shutdown
    logger.info("\n🛑 Shutting down...")
    shutdown = True


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ── Run Once (for testing) ────────────────

def run_once():
    """Single scan cycle — useful for testing data fetching."""
    console.print(f"\n[bold cyan]🔍 Single scan — {fmt_time()}[/bold cyan]\n")

    snap = api_client.get_snapshot()
    if not snap.slug:
        console.print("[red]❌ No active BTC 5m market found. Try again in a few seconds.[/red]")
        return

    # Show raw data
    console.print(f"Market: {snap.question}")
    console.print(f"Slug: {snap.slug}")
    console.print(f"BTC Price: ${snap.btc_price:,.0f}")
    console.print(f"YES Price: {snap.yes_price:.4f} | NO Price: {snap.no_price:.4f}")
    console.print(f"YES Orders: {snap.yes_orders} | YES Vol: ${snap.yes_volume:.2f}")
    console.print(f"NO Orders: {snap.no_orders} | NO Vol: ${snap.no_volume:.2f}")
    console.print(f"Total Wallets: {snap.total_wallets}")
    console.print(f"Time Remaining: T-{snap.seconds_remaining}s")
    console.print(f"Consensus: {snap.consensus_side or 'None'}")

    if snap.consensus_side:
        console.print(
            f"  Wallet%: {snap.consensus_wallet_pct:.1f}% | "
            f"Volume%: {snap.consensus_volume_pct:.1f}%"
        )

    # Evaluate strategy
    decision = trade_strategy.evaluate(snap)
    console.print(f"\nStrategy Decision: [bold]{decision['action']}[/bold]")
    if decision["action"] != "WAIT":
        console.print(f"  Side: {decision['side']}")
        console.print(f"  Price: {decision['price']:.4f}")
        console.print(f"  Size: {decision['size']}")
        console.print(f"  Reason: {decision['reason']}")

    # Show log format
    from dashboard import build_log_line
    console.print(f"\n[dim]Log:[/dim] {build_log_line(snap, decision, bool(snap.consensus_side))}")


# ── Execute Trade ─────────────────────────

def execute_entry(snap: MarketSnapshot, decision: dict):
    """Execute a market entry based on strategy decision."""
    side = decision["side"]
    price = decision["price"]
    size = decision["size"]

    token_id = snap.token_id_yes if side == "YES" else snap.token_id_no

    logger.info(f"🚀 ENTRY: {side} {size} shares @ {price:.2f}")

    result = api_client.place_order(
        token_id=token_id,
        side="BUY",
        price=price,
        size=size,
    )

    if result.success:
        console.print(
            f"[TRADE] [bold green]FILLED[/bold green] {side} "
            f"{result.filled} tokens @ {result.price:.2f} "
            f"(cost ${result.cost:.3f})"
            + (" [dim](DRY RUN)[/dim]" if result.dry_run else "")
        )
        trade_strategy.record_entry(snap, result)
    else:
        console.print(f"[TRADE] [bold red]FAILED[/bold red]: {result.error}")


def execute_exit(snap: MarketSnapshot, reason: str):
    """Execute position exit (sell)."""
    pos = trade_strategy.position
    if not pos.is_open:
        return

    current_price = snap.yes_price if pos.entry_side == "YES" else snap.no_price
    side_label = "TAKE PROFIT" if reason == "TP" else "STOP LOSS"

    logger.info(f"💰 {side_label}: SELL {pos.entry_side} {pos.size} @ {current_price:.2f}")

    result = api_client.place_order(
        token_id=pos.token_id,
        side="SELL",
        price=current_price,
        size=pos.size,
    )

    if result.success:
        pnl = (current_price - pos.entry_price) * pos.size
        color = "green" if pnl > 0 else "red"
        console.print(
            f"[TRADE] [bold {color}]{side_label}[/bold {color}] "
            f"SELL {result.filled} @ {result.price:.2f} | "
            f"P&L: [bold {color}]${pnl:+.3f}[/bold {color}]"
            + (" [dim](DRY RUN)[/dim]" if result.dry_run else "")
        )
        trade_strategy.record_exit(snap, result)
    else:
        console.print(f"[TRADE] [bold red]EXIT FAILED[/bold red]: {result.error}")


# ── Main Loop ─────────────────────────────

def run_loop():
    """Main trading loop with live dashboard."""
    global shutdown

    console.print("[bold cyan]╔══════════════════════════════════════════╗[/bold cyan]")
    console.print("[bold cyan]║   Polymarket BTC 5m Consensus Bot       ║[/bold cyan]")
    console.print("[bold cyan]╚══════════════════════════════════════════╝[/bold cyan]")
    console.print(f"Mode: [yellow]DRY RUN[/yellow]" if bot_config.DRY_RUN else "Mode: [red]LIVE TRADING[/red]")
    console.print(f"Market: {bot_config.MARKET_SLUG}")
    console.print(f"Scan interval: {bot_config.SCAN_INTERVAL}s")
    console.print(f"Min Consensus: {strategy_config.MIN_CONSENSUS}%")
    console.print()

    # Check credentials for live mode
    if not bot_config.DRY_RUN:
        if not bot_config.PRIVATE_KEY:
            console.print("[red]❌ PRIVATE_KEY not set! Switching to DRY RUN.[/red]")
            bot_config.DRY_RUN = True
        else:
            console.print("[yellow]⚠️  LIVE TRADING MODE — real orders will be placed![/yellow]")
            console.print()

    # Start dashboard
    dashboard.start()

    try:
        while not shutdown:
            # Fetch snapshot
            snap = api_client.get_snapshot()

            if not snap.slug:
                console.print("[yellow]⚠️  No market data, retrying...[/yellow]")
                dashboard.refresh()
                time.sleep(bot_config.SCAN_INTERVAL)
                continue

            # Strategy evaluation
            decision = trade_strategy.evaluate(snap)

            # Execute based on decision
            if decision["action"] == "ENTER":
                dashboard.update(snap, decision)
                dashboard.refresh()
                execute_entry(snap, decision)

            elif decision["action"] in ("EXIT_TP", "EXIT_SL"):
                reason = "TP" if decision["action"] == "EXIT_TP" else "SL"
                dashboard.update(snap, decision)
                dashboard.refresh()
                execute_exit(snap, reason)

            else:
                # Just update dashboard
                dashboard.update(snap, decision)
                dashboard.refresh()

            # Sleep
            for _ in range(bot_config.SCAN_INTERVAL):
                if shutdown:
                    break
                time.sleep(1)

    except KeyboardInterrupt:
        pass
    finally:
        dashboard.stop()
        console.print("\n[bold]👋 Bot stopped.[/bold]")
        if trade_strategy.position.is_open:
            console.print(f"[yellow]⚠️  Open position: {trade_strategy.position.entry_side} "
                          f"{trade_strategy.position.size} @ {trade_strategy.position.entry_price:.2f}[/yellow]")


# ── Entrypoint ────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Polymarket BTC 5m Consensus Bot")
    parser.add_argument("--live", action="store_true", help="Force live trading")
    parser.add_argument("--dry", action="store_true", help="Force dry run")
    parser.add_argument("--once", action="store_true", help="Single scan + exit")
    args = parser.parse_args()

    if args.live:
        bot_config.DRY_RUN = False
    elif args.dry:
        bot_config.DRY_RUN = True

    if args.once:
        run_once()
    else:
        run_loop()


if __name__ == "__main__":
    main()
