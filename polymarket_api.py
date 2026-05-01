"""
Polymarket API client — fetches real market data & executes orders.
Uses Gamma API for market data and CLOB API for orderbook + trading.
"""
import time
import logging
from typing import Optional
from dataclasses import dataclass, field

import requests
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import OrderArgs, PartialCreateOrderOptions
from py_clob_client_v2.order_builder.constants import BUY, SELL

from config import bot_config, strategy_config

logger = logging.getLogger("polybot.api")


# ──────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────

@dataclass
class MarketSnapshot:
    """A snapshot of the current market state."""
    slug: str = ""
    question: str = ""
    condition_id: str = ""
    token_id_yes: str = ""
    token_id_no: str = ""
    # Prices from Gamma
    yes_price: float = 0.0
    no_price: float = 0.0
    # Orderbook stats
    yes_orders: int = 0
    no_orders: int = 0
    yes_volume: float = 0.0
    no_volume: float = 0.0
    total_wallets: int = 0
    # Consensus
    consensus_side: str = ""         # "YES", "NO", or ""
    consensus_wallet_pct: float = 0.0
    consensus_volume_pct: float = 0.0
    # Market timing
    market_start_ts: int = 0
    market_end_ts: int = 0
    seconds_remaining: int = 0
    # BTC price
    btc_price: float = 0.0
    # Raw
    best_bid: float = 0.0
    best_ask: float = 0.0
    last_trade: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class TradeResult:
    """Result of a trade attempt."""
    success: bool = False
    side: str = ""
    price: float = 0.0
    size: float = 0.0
    cost: float = 0.0
    token_id: str = ""
    order_id: str = ""
    filled: float = 0.0
    dry_run: bool = True
    error: str = ""


# ──────────────────────────────────────────────
# Polymarket API Client
# ──────────────────────────────────────────────

class PolymarketClient:
    """Client for Polymarket Gamma + CLOB APIs."""

    def __init__(self):
        self.gamma = bot_config.GAMMA_API
        self.clob_url = bot_config.CLOB_API
        self.dry_run = bot_config.DRY_RUN
        self._clob: Optional[ClobClient] = None
        self._market_cache: dict = {}

    # ── Gamma API ──────────────────────────

    def _gamma_get(self, path: str, params: dict = None) -> dict | list:
        url = f"{self.gamma}{path}"
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Gamma API error [{path}]: {e}")
            return {}

    def get_market_by_slug(self, slug: str) -> dict:
        """Fetch market data from Gamma API by exact slug."""
        cache_key = f"slug:{slug}"
        if cache_key in self._market_cache:
            cached, ts = self._market_cache[cache_key]
            if time.time() - ts < 2:
                return cached

        data = self._gamma_get("/markets", {"slug": slug})
        if isinstance(data, list) and len(data) > 0:
            self._market_cache[cache_key] = (data[0], time.time())
            return data[0]
        return {}

    def find_active_btc_market(self) -> dict:
        """
        Find the currently active BTC 5m market.
        BTC 5m markets have slugs: btc-updown-5m-<unix_start_timestamp>
        Markets are on 5-minute boundaries in UTC.
        """
        now = int(time.time())
        # Current 5-minute boundary
        boundary = (now // 300) * 300

        # Try current + surrounding boundaries
        for offset in [0, 300, -300, 600, -600]:
            bs = boundary + offset
            slug = f"btc-updown-5m-{bs}"
            market = self.get_market_by_slug(slug)
            if market:
                # Check if market is still active (not closed, accepting orders)
                if market.get("active") and not market.get("closed"):
                    return market

        return {}

    # ── CLOB Orderbook ─────────────────────

    def _clob_get(self, path: str, params: dict = None) -> dict:
        url = f"{self.clob_url}{path}"
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"CLOB API error [{path}]: {e}")
            return {}

    def get_orderbook(self, token_id: str) -> dict:
        """Fetch orderbook for a specific token."""
        return self._clob_get("/book", {"token_id": token_id})

    # ── BTC Price ──────────────────────────

    def get_btc_price(self) -> float:
        """Fetch live BTC price from Binance (free, no key needed)."""
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
                timeout=5,
            )
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception:
            try:
                r = requests.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "bitcoin", "vs_currencies": "usd"},
                    timeout=5,
                )
                r.raise_for_status()
                return float(r.json()["bitcoin"]["usd"])
            except Exception as e:
                logger.error(f"BTC price fetch failed: {e}")
                return 0.0

    # ── USDC Balance ───────────────────────

    def get_usdc_balance(self) -> float:
        """Get USDC balance from Polymarket (requires auth)."""
        if self.dry_run:
            return 1000.0
        try:
            clob = self._get_clob_client()
            if clob is None:
                return 0.0
            balance = clob.get_balance_allowance()
            return float(balance.get("balance", 0))
        except Exception as e:
            logger.error(f"Balance fetch failed: {e}")
            return 0.0

    # ── CLOB Client (Authenticated) ────────

    def _get_clob_client(self) -> Optional[ClobClient]:
        """Create or return cached authenticated CLOB client."""
        if self._clob is not None:
            return self._clob

        pk = bot_config.PRIVATE_KEY
        if not pk:
            logger.warning("PRIVATE_KEY not set — trading disabled")
            return None

        try:
            funder = bot_config.POLY_FUNDER or None
            sig_type = bot_config.SIGNATURE_TYPE

            self._clob = ClobClient(
                host=self.clob_url,
                key=pk,
                chain_id=bot_config.CHAIN_ID,
                signature_type=sig_type,
                funder=funder,
            )
            creds = self._clob.create_or_derive_api_creds()
            self._clob.set_api_creds(creds)
            logger.info("CLOB client authenticated ✓")
            return self._clob
        except Exception as e:
            logger.error(f"CLOB auth failed: {e}")
            return None

    # ── Trading ────────────────────────────

    def place_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> TradeResult:
        """Place an order on Polymarket. side: "BUY" or "SELL" """
        result = TradeResult(
            side=side, price=price, size=size,
            cost=price * size, token_id=token_id, dry_run=self.dry_run,
        )

        if self.dry_run:
            logger.info(
                f"[DRY RUN] {side} {size} shares @ {price:.2f} (cost ${price * size:.3f})"
            )
            result.success = True
            result.filled = size
            result.order_id = f"dry-{int(time.time()*1000)}"
            return result

        clob = self._get_clob_client()
        if clob is None:
            result.error = "CLOB client not authenticated"
            return result

        try:
            order_args = OrderArgs(
                token_id=token_id, price=price, size=size,
                side=BUY if side.upper() == "BUY" else SELL,
            )
            options = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)
            order = clob.create_and_post_order(order_args, options)
            result.success = True
            result.order_id = order.get("orderID", order.get("id", ""))
            result.filled = float(order.get("filled", size))
            result.cost = float(order.get("totalCost", price * size))
            logger.info(
                f"[REAL] Order: {result.order_id} | {side} {result.filled} "
                f"@ {price:.2f} (cost ${result.cost:.3f})"
            )
        except Exception as e:
            result.error = str(e)
            logger.error(f"Order failed: {e}")

        return result

    # ── Snapshot Builder ───────────────────

    def get_snapshot(self) -> MarketSnapshot:
        """Build a full market snapshot from Gamma + CLOB data."""
        import json
        snap = MarketSnapshot()

        # 1. Find active BTC 5m market
        market = self.find_active_btc_market()
        if not market:
            logger.warning("No active BTC 5m market found")
            return snap

        snap.slug = market.get("slug", "")
        snap.question = market.get("question", "")
        snap.condition_id = market.get("conditionId", "")

        # Parse token IDs
        clob_ids = market.get("clobTokenIds", "[]")
        try:
            ids = json.loads(clob_ids) if isinstance(clob_ids, str) else clob_ids
            snap.token_id_yes = ids[0] if len(ids) > 0 else ""
            snap.token_id_no = ids[1] if len(ids) > 1 else ""
        except Exception:
            pass

        # Prices
        prices_str = market.get("outcomePrices", "[]")
        try:
            prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
            snap.yes_price = float(prices[0]) if len(prices) > 0 else 0.0
            snap.no_price = float(prices[1]) if len(prices) > 1 else 0.0
        except Exception:
            pass

        snap.best_bid = float(market.get("bestBid", 0))
        snap.best_ask = float(market.get("bestAsk", 0))
        snap.last_trade = float(market.get("lastTradePrice", 0))
        snap.volume_24h = float(market.get("volume24hr", 0))
        snap.liquidity = float(market.get("liquidity", 0))

        # Market timing — use eventStartTime for accurate countdown
        event_start = market.get("eventStartTime", "")
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
            snap.market_start_ts = int(dt.timestamp())
            snap.market_end_ts = snap.market_start_ts + 300  # end = start + 5min
        except Exception:
            pass
        end_str = market.get("endDate", "")
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            snap.market_end_ts = int(dt.timestamp())
        except Exception:
            pass
        snap.seconds_remaining = max(0, snap.market_end_ts - int(time.time()))

        # 2. Get orderbooks for both YES and NO tokens
        yes_book = {}
        no_book = {}
        if snap.token_id_yes:
            yes_book = self.get_orderbook(snap.token_id_yes)
        if snap.token_id_no:
            no_book = self.get_orderbook(snap.token_id_no)

        def _parse_book(book: dict) -> tuple:
            """Return (order_count, total_volume) from orderbook."""
            orders = 0
            volume = 0.0
            for side_key in ("bids", "asks"):
                for entry in book.get(side_key, []):
                    orders += 1
                    volume += float(entry.get("size", 0))
            return orders, volume

        snap.yes_orders, snap.yes_volume = _parse_book(yes_book)
        snap.no_orders, snap.no_volume = _parse_book(no_book)
        snap.total_wallets = snap.yes_orders + snap.no_orders

        # 3. Consensus calculation
        total_orders = snap.yes_orders + snap.no_orders
        total_volume = snap.yes_volume + snap.no_volume

        if total_orders > 0 and total_volume > 0:
            yes_w_pct = (snap.yes_orders / total_orders) * 100
            no_w_pct = (snap.no_orders / total_orders) * 100
            yes_v_pct = (snap.yes_volume / total_volume) * 100
            no_v_pct = (snap.no_volume / total_volume) * 100

            min_cons = strategy_config.MIN_CONSENSUS
            if yes_w_pct >= min_cons and yes_v_pct >= min_cons:
                snap.consensus_side = "YES"
                snap.consensus_wallet_pct = yes_w_pct
                snap.consensus_volume_pct = yes_v_pct
            elif no_w_pct >= min_cons and no_v_pct >= min_cons:
                snap.consensus_side = "NO"
                snap.consensus_wallet_pct = no_w_pct
                snap.consensus_volume_pct = no_v_pct

        # 4. BTC price
        snap.btc_price = self.get_btc_price()
        snap.timestamp = time.time()

        return snap


# Global client instance
client = PolymarketClient()
