# Polymarket BTC 5m Consensus Trading Bot

Bot trading Polymarket untuk market **BTC Up/Down 5 menit** dengan strategi konsensus.

## Cara Kerja

1. Scan orderbook YES & NO dari CLOB Polymarket
2. Hitung konsensus: wallet% dan volume% > threshold (62%)
3. Entry di window T-100s sampai T-10s sebelum market close
4. TP di 95¢, dynamic stop loss (consensus flip)

---

## 🖥️ Setup Full dari Nol (VPS Ubuntu/Debian)

### 1. SSH + Update Sistem

```bash
ssh root@<ip-vps>
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git curl screen
```

> **Login sebagai non-root?** Tambahin `sudo` di depan command `apt`.

### 2. Clone Repo & Install

```bash
git clone https://github.com/daffafirmansyah/polymarketsensus.git
cd polymarketsensus
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### 3. Isi Private Key

```bash
nano .env
```

Isi minimal 3 baris:

```env
PRIVATE_KEY=0x<PRI…-KEY-LO>
POLY_FUNDER=0x<POLYMARKET-ADDRESS-LO>
SIGNATURE_TYPE=2
```

> **Cara dapetin:**
> - `PRIVATE_KEY` → MetaMask → Account Details → Show Private Key
> - `POLY_FUNDER` → [polymarket.com/wallet](https://polymarket.com/wallet) → copy "Polymarket Address" (beda dari wallet address)
> - `SIGNATURE_TYPE` → `0` = EOA, `1` = email, **`2`** = web3 wallet (MetaMask/Rabby)

Simpan: `Ctrl+O` → `Enter` → `Ctrl+X`

### 4. Test Dry Run

```bash
python bot.py --once
```

Output yang diharapkan:

```
🔍 Single scan — 21:55:38
Market: Bitcoin Up or Down - May 1, 9:55AM-10:00AM ET
BTC Price: $78,894
YES: 0.505 | NO: 0.495 | Wallets: 198
Time Remaining: T-262s
Strategy Decision: WAIT
```

### 5. Test Dashboard Live (Dry Run)

```bash
python bot.py --dry
```

Tampil 4 panel + live log:
- **📡 Market** — market aktif, countdown, YES/NO prices
- **📊 Dashboard** — BTC price, USDC balance, P&L W/L, position
- **🎯 Consensus** — alert kalo consensus tercapai
- **⚙️ Strategy** — parameter aktif

### 6. Live Trading 🚀

```bash
python bot.py --live
```

⚠️ **Ini beneran eksekusi order pake duit lo.** Pastiin udah deposit USDC di Polymarket.

### 7. Jalankan 24/7 dengan Screen

```bash
# Bikin session baru
screen -S polybot

# Di dalam screen
source venv/bin/activate
python bot.py --live

# Detach: Ctrl+A lalu D
# Re-attach: screen -r polybot
# List sessions: screen -ls
```

Atau pake **tmux**:

```bash
tmux new -s polybot
source venv/bin/activate
python bot.py --live
# Detach: Ctrl+B lalu D
# Re-attach: tmux attach -t polybot
```

### 8. Cek Status

```bash
screen -r polybot          # re-attach screen
tmux attach -t polybot     # re-attach tmux
ps aux | grep bot.py       # cek process jalan
```

---

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

Semua parameter bisa diadjust di `.env`.

---

## Konfigurasi .env

```env
# ── Polymarket Auth (Wajib untuk live trading) ──
PRIVATE_KEY=          # Private key Polygon EOA
POLY_FUNDER=          # Polymarket proxy address
SIGNATURE_TYPE=2      # 0=EOA, 1=email, 2=web3 wallet

# ── Bot Config ──
DRY_RUN=true          # false untuk live trading
SCAN_INTERVAL=5       # Detik antar scan

# ── Strategy ──
MIN_WALLETS=100
MIN_CONSENSUS=62
TP_PRICE=0.95
MAX_ENTRY=0.80
ENTRY_WINDOW_START=100
ENTRY_WINDOW_END=10
MAX_TRADE=6
MIN_TRADE=5
MAX_POSITION=1
```

---

## Commands

```bash
python bot.py --once    # Single scan (test data)
python bot.py --dry     # Continuous dry run (simulasi)
python bot.py --live    # Live trading (order real)
python bot.py           # Default: ikutin DRY_RUN di .env
```
