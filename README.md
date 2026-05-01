# Polymarket BTC 5m Consensus Trading Bot

Bot trading Polymarket untuk market **BTC Up/Down 5 menit** dengan strategi konsensus.

## Cara Kerja

1. Scan orderbook YES & NO dari CLOB Polymarket
2. Hitung konsensus: wallet% dan volume% > threshold
3. Entry di window T-100s sampai T-10s sebelum market close
4. TP di 95¢, dynamic stop loss

## Quick Start

```bash
# Install
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Isi .env dengan private key Polygon
nano .env

# Test dry run (fetch data asli, ga eksekusi order)
python bot.py --once

# Live trading
python bot.py --live
```

## Strategy Parameters

| Parameter | Value | Keterangan |
|-----------|-------|------------|
| MIN_WALLETS | 100 | Filter minimal wallet |
| MIN_CONSENSUS | 62% | Threshold konsensus |
| TP | 95¢ | Take profit |
| MAX_ENTRY | 80¢ | Maksimal harga entry |
| Entry window | T-100s to T-10s | Window entry |
| Max trade | 6 shares | Maks per trade |
| Min trade | 5 shares | Min per trade |
| Max position | 1 | Anti double entry |

## Konfigurasi

Semua parameter bisa diadjust di `.env`:

```env
PRIVATE_KEY=        # Private key Polygon EOA
POLY_FUNDER=         # Proxy address (web3 wallet)
SIGNATURE_TYPE=2     # 0=EOA, 1=email, 2=web3 wallet
DRY_RUN=true         # false untuk live trading
SCAN_INTERVAL=5      # Detik antar scan
```
