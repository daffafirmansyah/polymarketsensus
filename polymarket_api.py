"""
Polymarket API client — fetches real market data & executes orders.
Uses Gamma API for market data and CLOB API for orderbook + trading.
Optimized with connection pooling for minimal latency.
"""
import time
import logging
from typing import Optional
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import OrderArgs, PartialCreateOrderOptions
from py_clob_client_v2.order_builder.constants import BUY, SELL

from config import bot_config, strategy_config
from btc_ws import get_btc_price as _get_btc_ws

# Data API base for holders (real wallet data)
DATA_API = "https://data-api.polymarket.com"

logger = logging.getLogger("polybot.api")

# Persistent HTTP session for connection reuse (keep-alive)
_session = None

def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        retry = Retry(total=2, backoff_factor=0.1)
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=retry)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session


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
    last_fetched: float = field(default_factory=time.time)
    # Live indicator
    is_live: bool = False  # True when data was just fetched
    # BTC price
    btc_price: float = 0.0
    # Raw
    best_bid: float = 0.0
    best_ask: float = 0.0
    no_best_bid: float = 0.0
    no_best_ask: float = 0.0
    # CLOB orderbook (real-time, every heartbeat)
    yes_ob_orders: int = 0
    no_ob_orders: int = 0
    yes_ob_vol: float = 0.0
    no_ob_vol: float = 0.0
    total_ob_vol: float = 0.0
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
            r = _get_session().get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"Gamma API error [{path}]: {e}")
            return {}

    def get_market_by_slug(self, slug: str) -> dict:
        """Fetch market data from Gamma API by exact slug — NO cache, always fresh."""
        data = self._gamma_get("/markets", {"slug": slug})
        if isinstance(data, list) and len(data) > 0:
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
            r = _get_session().get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"CLOB API error [{path}]: {e}")
            return {}

    def get_orderbook(self, token_id: str) -> dict:
        """Fetch orderbook for a specific token."""
        return self._clob_get("/book", {"token_id": token_id})

    def get_orderbooks_batch(self, token_ids: list[str]) -> list[dict]:
        """Fetch MULTIPLE orderbooks in ONE request (POST /books)."""
        url = f"{self.clob_url}/books"
        try:
            body = [{"token_id": tid} for tid in token_ids if tid]
            if not body:
                return []
            r = _get_session().post(url, json=body, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"CLOB batch books error: {e}")
            return []

    # ── BTC Price ──────────────────────────

    def get_holders(self, condition_id: str, limit: int = 2000) -> dict:
        """
        Fetch REAL wallet holders from Polymarket Data API.
        Returns individual wallet addresses with positions.
        Retries up to 3x on failure, caches last successful result.
        """
        # Return cached data for same condition_id if fresh (< 1s)
        cache_key = f"holders:{condition_id}"
        if cache_key in self._market_cache:
            cached, ts = self._market_cache[cache_key]
            if time.time() - ts < 1:
                return cached

        for attempt in range(3):
            try:
                r = _get_session().get(
                    f"{DATA_API}/holders",
                    params={"market": condition_id, "limit": limit},
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate",
                        "Pragma": "no-cache",
                    },
                    timeout=10,
                )
                r.raise_for_status()
                holders = r.json()

                yes_wallets = set()
                no_wallets = set()
                yes_volume = 0.0
                no_volume = 0.0

                for token_group in holders:
                    for h in token_group.get("holders", []):
                        addr = h.get("proxyWallet", "")
                        amt = float(h.get("amount", 0))
                        idx = h.get("outcomeIndex", -1)
                        if idx == 0:
                            yes_wallets.add(addr)
                            yes_volume += amt
                        elif idx == 1:
                            no_wallets.add(addr)
                            no_volume += amt

                result = {
                    "yes_wallets": len(yes_wallets),
                    "no_wallets": len(no_wallets),
                    "total_wallets": len(yes_wallets) + len(no_wallets),
                    "yes_volume": yes_volume,
                    "no_volume": no_volume,
                }

                # Cache for 1 second to avoid hammering API during retries
                self._market_cache[cache_key] = (result, time.time())
                return result

            except Exception as e:
                logger.warning(f"Holders fetch attempt {attempt+1}/3 failed: {e}")
                if attempt < 2:
                    time.sleep(0.5)

        # All retries failed — return last cached or empty
        logger.error("Holders fetch failed after 3 retries")
        if cache_key in self._market_cache:
            cached, _ = self._market_cache[cache_key]
            return cached
        return {}

    def get_btc_price(self) -> float:
        """Get BTC price from Binance WebSocket (real-time push)."""
        price = _get_btc_ws()
        if price > 0:
            return price
        # Fallback: REST
        return self._get_btc_rest()

    def _get_btc_rest(self) -> float:
        """Fallback: Binance REST API."""
        try:
            r = _get_session().get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
                timeout=5,
            )
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception:
            try:
                r = _get_session().get(
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

    def has_sufficient_balance(self, cost: float) -> tuple[bool, float]:
        """Check if we have enough USDC for a trade. Returns (can_afford, balance)."""
        if self.dry_run:
            return True, 1000.0
        balance = self.get_usdc_balance()
        # Add 5% buffer for fees
        return balance >= cost * 1.05, balance

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

            result.order_id = order.get("orderID", order.get("id", ""))
            actual_filled = float(order.get("filled", 0))
            actual_price = float(order.get("price", price))

            if actual_filled > 0:
                result.success = True
                result.filled = actual_filled
                result.price = actual_price
                result.cost = actual_price * actual_filled
                logger.info(
                    f"[LIVE] {side} {result.filled}/{size} filled @ {actual_price:.3f} "
                    f"(cost ${result.cost:.3f}) id={result.order_id[:12]}"
                )
            else:
                result.error = f"Order placed but 0 filled (id: {result.order_id[:12]})"
                logger.warning(result.error)
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

        # Prices (set later from CLOB last_trade, not Gamma midpoint)
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

        # 2. Get REAL wallet data from Data API (holders) — ONLY source, no fallback
        holders_data = self.get_holders(snap.condition_id) if snap.condition_id else {}

        if holders_data:
            snap.yes_orders = holders_data.get("yes_wallets", 0)
            snap.no_orders = holders_data.get("no_wallets", 0)
            snap.total_wallets = holders_data.get("total_wallets", 0)
            snap.yes_volume = holders_data.get("yes_volume", 0)
            snap.no_volume = holders_data.get("no_volume", 0)

        # 3. Get CLOB orderbooks in ONE batched request (POST /books)
        token_ids = [t for t in [snap.token_id_yes, snap.token_id_no] if t]
        books = self.get_orderbooks_batch(token_ids) if token_ids else []

        yes_book = books[0] if len(books) > 0 else {}
        no_book = books[1] if len(books) > 1 else {}

        # Extract bid/ask arrays
        yes_bids = yes_book.get("bids", [])
        yes_asks = yes_book.get("asks", [])
        no_bids = no_book.get("bids", [])
        no_asks = no_book.get("asks", [])

        # Best bid = highest bid price (last in ascending); Best ask = lowest ask (last in descending)
        snap.best_bid = float(yes_bids[-1]["price"]) if yes_bids else 0.0
        snap.best_ask = float(yes_asks[-1]["price"]) if yes_asks else 0.0
        snap.no_best_bid = float(no_bids[-1]["price"]) if no_bids else 0.0
        snap.no_best_ask = float(no_asks[-1]["price"]) if no_asks else 0.0

        # YES/NO prices from CLOB last_trade (real-time)
        # Binary market: NO ≈ 1.0 - YES. Use YES last_trade as primary.
        yes_last = float(yes_book.get("last_trade_price", 0) or 0)
        no_last = float(no_book.get("last_trade_price", 0) or 0)

        if yes_last > 0:
            snap.yes_price = yes_last
            snap.no_price = max(0, round(1.0 - yes_last, 4))
        elif no_last > 0:
            snap.no_price = no_last
            snap.yes_price = max(0, round(1.0 - no_last, 4))
        else:
            # Fallback to Gamma midpoint
            prices_str = market.get("outcomePrices", "[]")
            try:
                import json
                prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
                snap.yes_price = float(prices[0]) if len(prices) > 0 else 0.0
                snap.no_price = float(prices[1]) if len(prices) > 1 else 0.0
            except Exception:
                pass

        # Orderbook real-time metrics
        snap.yes_ob_orders = len(yes_bids)
        snap.no_ob_orders = len(no_bids)
        snap.yes_ob_vol = sum(float(e.get("size", 0)) for e in yes_bids)
        snap.no_ob_vol = sum(float(e.get("size", 0)) for e in no_bids)
        snap.total_ob_vol = snap.yes_ob_vol + snap.no_ob_vol

        # 3. Consensus — uses CLOB real-time orders + volume (strategy still uses holders for MIN_WALLETS)
        total_ob_orders = snap.yes_ob_orders + snap.no_ob_orders
        total_ob_vol = snap.total_ob_vol

        if total_ob_orders > 0 and total_ob_vol > 0:
            yes_w_pct = (snap.yes_ob_orders / total_ob_orders) * 100
            no_w_pct = (snap.no_ob_orders / total_ob_orders) * 100
            yes_v_pct = (snap.yes_ob_vol / total_ob_vol) * 100
            no_v_pct = (snap.no_ob_vol / total_ob_vol) * 100

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
        snap.last_fetched = time.time()  # for display
        return snap


# Global client instance
client = PolymarketClient()
