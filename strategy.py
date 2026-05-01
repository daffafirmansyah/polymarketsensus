"""
Strategy engine — decides when to enter/exit based on consensus.
"""
import time
import logging
from dataclasses import dataclass, field
from enum import Enum

from config import strategy_config, StrategyConfig
from polymarket_api import MarketSnapshot, TradeResult

logger = logging.getLogger("polybot.strategy")


class PositionState(Enum):
    FLAT = "flat"
    LONG_YES = "long_yes"
    LONG_NO = "long_no"


@dataclass
class Position:
    """Track current position state."""
    state: PositionState = PositionState.FLAT
    entry_price: float = 0.0
    entry_side: str = ""
    size: float = 0.0
    cost: float = 0.0
    token_id: str = ""
    order_id: str = ""
    entry_time: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.state != PositionState.FLAT

    def close(self):
        self.state = PositionState.FLAT
        self.entry_price = 0.0
        self.entry_side = ""
        self.size = 0.0
        self.cost = 0.0
        self.token_id = ""
        self.order_id = ""
        self.entry_time = 0.0


class Strategy:
    """Consensus-based trading strategy for Polymarket BTC 5m."""

    def __init__(self):
        self.cfg: StrategyConfig = strategy_config
        self.position = Position()
        self.total_trades = 0
        self.total_wins = 0
        self.total_losses = 0
        self.total_pnl = 0.0
        self._last_exit_attempt = 0.0
        self._exit_retries = 0
        self._max_exit_retries = 3

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.total_wins / self.total_trades) * 100

    # ── Entry Logic ────────────────────────

    def should_enter(self, snap: MarketSnapshot) -> tuple[bool, str, float]:
        """
        Check if we should enter a trade.
        Returns (should_enter, side, entry_price).

        Conditions:
        1. Consensus reached (wallet% AND volume% > MIN_CONSENSUS)
        2. Within entry window (T-100s to T-10s)
        3. Entry price < MAX_ENTRY
        4. Total wallets > MIN_WALLETS
        5. No existing position (MAX_POSITION = 1)
        """
        # Condition 1: Consensus
        if not snap.consensus_side:
            return False, "", 0.0

        # Condition 2: Entry window
        remaining = snap.seconds_remaining
        if remaining > self.cfg.ENTRY_WINDOW_START:
            return False, "", 0.0
        if remaining < self.cfg.ENTRY_WINDOW_END:
            return False, "", 0.0

        # Condition 3: Wallets
        if snap.total_wallets < self.cfg.MIN_WALLETS:
            return False, "", 0.0

        # Condition 4: No existing position
        if self.position.is_open:
            return False, "", 0.0

        # Condition 5: Entry price check
        side = snap.consensus_side
        entry_price = snap.yes_price if side == "YES" else snap.no_price
        if entry_price > self.cfg.MAX_ENTRY:
            return False, "", 0.0

        return True, side, entry_price

    def get_position_size(self, entry_price: float) -> float:
        """Calculate position size based on max trade shares."""
        return float(self.cfg.MIN_TRADE)  # Use min trade for safety

    # ── Exit / Take Profit ────────────────

    def can_attempt_exit(self) -> bool:
        """Prevent repeated exit attempts — cooldown 3s, max 3 retries."""
        if self._exit_retries >= self._max_exit_retries:
            return False
        if time.time() - self._last_exit_attempt < 3:
            return False
        return True

    def record_exit_attempt(self):
        self._last_exit_attempt = time.time()
        self._exit_retries += 1

    def reset_exit_retries(self):
        self._exit_retries = 0

    def should_take_profit(self, snap: MarketSnapshot) -> bool:
        """Check if current position has hit take profit."""
        if not self.position.is_open:
            return False

        current_price = snap.yes_price if self.position.entry_side == "YES" else snap.no_price

        if current_price >= self.cfg.TP_PRICE:
            return True

        return False

    def should_stop_loss(self, snap: MarketSnapshot) -> bool:
        """Dynamic stop loss — if consensus flips strongly against us."""
        if not self.position.is_open:
            return False

        # If consensus now favors the opposite side strongly (>80%)
        if snap.consensus_side and snap.consensus_side != self.position.entry_side:
            if snap.consensus_wallet_pct > 75 and snap.consensus_volume_pct > 75:
                return True

        # If market has ended
        if snap.seconds_remaining <= 0:
            return True

        return False

    # ── Record Results ────────────────────

    def record_entry(self, snap: MarketSnapshot, result: TradeResult):
        """Record a new position entry."""
        self.position = Position(
            state=PositionState.LONG_YES if result.side == "YES" else PositionState.LONG_NO,
            entry_price=result.price,
            entry_side=result.side,
            size=result.filled,
            cost=result.cost,
            token_id=result.token_id,
            order_id=result.order_id,
            entry_time=time.time(),
        )
        self.total_trades += 1
        self._exit_retries = 0  # reset for new position
        self._last_exit_attempt = 0
        logger.info(f"📊 Position opened: {result.side} {result.filled} @ {result.price:.2f}")

    def record_exit(self, snap: MarketSnapshot, result: TradeResult):
        """Record position exit and calculate P&L."""
        if not self.position.is_open:
            return

        # P&L: for YES: sell_price - buy_price; for NO: same logic
        pnl = (result.price - self.position.entry_price) * self.position.size
        self.total_pnl += pnl

        if pnl > 0:
            self.total_wins += 1
        else:
            self.total_losses += 1

        logger.info(
            f"📊 Position closed: P&L ${pnl:.3f} | "
            f"Total P&L: ${self.total_pnl:.3f} | "
            f"W/L: {self.total_wins}/{self.total_losses}"
        )
        self.position.close()

    # ── Snapshot Check (per heartbeat) ─────

    def evaluate(self, snap: MarketSnapshot) -> dict:
        """
        Full evaluation cycle — called each heartbeat.
        Returns decision dict for logging/dashboard.
        """
        decision = {
            "action": "WAIT",
            "side": "",
            "price": 0.0,
            "size": 0.0,
            "reason": "",
        }

        # Check exit first (with cooldown to prevent spam)
        # Market end always triggers immediate exit (bypass cooldown)
        if self.position.is_open:
            market_ended = snap.seconds_remaining <= 0
            if market_ended or self.can_attempt_exit():
                if self.should_take_profit(snap):
                    decision["action"] = "EXIT_TP"
                    decision["side"] = "SELL"
                    decision["price"] = snap.yes_price if self.position.entry_side == "YES" else snap.no_price
                    decision["size"] = self.position.size
                    decision["reason"] = "Take profit hit"
                    return decision

                if market_ended or self.should_stop_loss(snap):
                    decision["action"] = "EXIT_SL"
                    decision["side"] = "SELL"
                    decision["price"] = snap.yes_price if self.position.entry_side == "YES" else snap.no_price
                    decision["size"] = self.position.size
                    decision["reason"] = "Market ended" if market_ended else "Stop loss triggered"
                    return decision

        # Check entry
        enter, side, price = self.should_enter(snap)
        if enter:
            decision["action"] = "ENTER"
            decision["side"] = side
            decision["price"] = price
            decision["size"] = self.get_position_size(price)
            decision["reason"] = (
                f"Consensus {side} | "
                f"W:{snap.consensus_wallet_pct:.0f}% V:{snap.consensus_volume_pct:.0f}% | "
                f"T-{snap.seconds_remaining}s"
            )

        return decision


# Singleton
strategy = Strategy()
