"""
Real-time BTC price via Binance WebSocket (push) + Yahoo Finance fallback.
Runs in background thread, updates shared price atomically.
"""
import json
import logging
import threading
import time
from websocket import WebSocketApp

import requests

logger = logging.getLogger("polybot.btcws")

# Shared atomic BTC price
_btc_price = 0.0
_btc_price_ts = 0.0
_lock = threading.Lock()
_ws = None
_running = False


def get_btc_price() -> float:
    """Thread-safe read of latest BTC price."""
    with _lock:
        return _btc_price


def get_btc_price_age() -> float:
    """Seconds since last price update."""
    with _lock:
        return time.time() - _btc_price_ts if _btc_price_ts else 999


# ── Binance WebSocket ─────────────────────

def _on_message(ws_app, message):
    global _btc_price, _btc_price_ts
    try:
        data = json.loads(message)
        # Binance ticker: "c" = last price, "p" = price change
        price = float(data.get("c", 0))
        if price > 0:
            with _lock:
                _btc_price = price
                _btc_price_ts = time.time()
    except Exception:
        pass


def _on_error(ws_app, error):
    logger.warning(f"Binance WS error: {error}")


def _on_close(ws_app, close_status_code, close_msg):
    global _running
    logger.warning(f"Binance WS closed: {close_status_code} {close_msg}")
    _running = False


def _on_open(ws_app):
    logger.info("Binance WebSocket connected ✓")
    # Subscribe to BTC/USDT ticker (real-time price)
    ws_app.send(json.dumps({
        "method": "SUBSCRIBE",
        "params": ["btcusdt@ticker"],
        "id": 1,
    }))


def _run_ws():
    global _ws, _running
    while _running:
        try:
            _ws = WebSocketApp(
                "wss://stream.binance.com:9443/ws",
                on_open=_on_open,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            _ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            logger.error(f"Binance WS crash: {e}")
        if _running:
            time.sleep(5)  # reconnect delay


# ── Yahoo Finance Fallback ────────────────

def _fetch_yahoo() -> float:
    """Fetch BTC price from Yahoo Finance (REST fallback)."""
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/BTC-USD",
            params={"range": "1d", "interval": "1m"},
            timeout=5,
        )
        r.raise_for_status()
        result = r.json()
        meta = result["chart"]["result"][0]["meta"]
        return float(meta.get("regularMarketPrice", 0))
    except Exception as e:
        logger.warning(f"Yahoo fallback failed: {e}")
        return 0.0


# ── Fallback monitor thread ───────────────

def _fallback_monitor():
    """If Binance WS is stale (>10s), fetch from Yahoo."""
    global _btc_price, _btc_price_ts
    while _running:
        time.sleep(5)
        age = get_btc_price_age()
        if age > 10:
            logger.warning(f"Binance WS stale ({age:.0f}s), trying Yahoo fallback...")
            price = _fetch_yahoo()
            if price > 0:
                with _lock:
                    if get_btc_price_age() > 10:  # still stale?
                        _btc_price = price
                        _btc_price_ts = time.time()
                logger.info(f"Yahoo fallback BTC: ${price:,.0f}")


# ── Start / Stop ──────────────────────────

def start():
    """Start Binance WebSocket + fallback monitor threads."""
    global _running
    if _running:
        return
    _running = True
    threading.Thread(target=_run_ws, daemon=True, name="binance-ws").start()
    threading.Thread(target=_fallback_monitor, daemon=True, name="btc-fallback").start()
    logger.info("BTC price streams started (Binance WS + Yahoo fallback)")


def stop():
    global _running, _ws
    _running = False
    if _ws:
        _ws.close()
