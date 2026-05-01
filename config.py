"""
Polymarket BTC 5m Trading Bot - Configuration & Strategy Parameters
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class StrategyConfig:
    """Strategy parameters for BTC 5m consensus trading."""
    MIN_WALLETS: int = 100
    MIN_CONSENSUS: float = 62.0       # percentage
    TP_PRICE: float = 0.95            # take profit at 95 cents
    MAX_ENTRY: float = 0.80           # max entry price in cents
    ENTRY_WINDOW_START: int = 100     # seconds before market close
    ENTRY_WINDOW_END: int = 10        # seconds before market close
    MAX_TRADE: float = 1.50           # max USDC per trade
    MAX_POSITION: int = 1             # max concurrent positions
    MIN_TRADE: float = 1.05           # min USDC per trade

    @classmethod
    def from_env(cls) -> "StrategyConfig":
        return cls(
            MIN_WALLETS=int(os.getenv("MIN_WALLETS", "100")),
            MIN_CONSENSUS=float(os.getenv("MIN_CONSENSUS", "62")),
            TP_PRICE=float(os.getenv("TP_PRICE", "0.95")),
            MAX_ENTRY=float(os.getenv("MAX_ENTRY", "0.80")),
            ENTRY_WINDOW_START=int(os.getenv("ENTRY_WINDOW_START", "100")),
            ENTRY_WINDOW_END=int(os.getenv("ENTRY_WINDOW_END", "10")),
            MAX_TRADE=float(os.getenv("MAX_TRADE", "1.50")),
            MAX_POSITION=int(os.getenv("MAX_POSITION", "1")),
            MIN_TRADE=float(os.getenv("MIN_TRADE", "1.05")),
        )


@dataclass
class BotConfig:
    """Bot configuration loaded from environment."""
    PRIVATE_KEY: str = ""
    POLY_FUNDER: str = ""
    SIGNATURE_TYPE: int = 2
    DRY_RUN: bool = True
    MARKET_SLUG: str = "btc-updown-5m"
    SCAN_INTERVAL: int = 5

    # API endpoints
    GAMMA_API: str = "https://gamma-api.polymarket.com"
    CLOB_API: str = "https://clob.polymarket.com"
    CHAIN_ID: int = 137

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            PRIVATE_KEY=os.getenv("PRIVATE_KEY", ""),
            POLY_FUNDER=os.getenv("POLY_FUNDER", ""),
            SIGNATURE_TYPE=int(os.getenv("SIGNATURE_TYPE", "2")),
            DRY_RUN=os.getenv("DRY_RUN", "true").lower() == "true",
            MARKET_SLUG=os.getenv("MARKET_SLUG", "btc-updown-5m"),
            SCAN_INTERVAL=int(os.getenv("SCAN_INTERVAL", "5")),
        )


# Singleton instances
bot_config = BotConfig.from_env()
strategy_config = StrategyConfig.from_env()
