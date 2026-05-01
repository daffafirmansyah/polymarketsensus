#!/bin/bash
# Quick start script for Polymarket BTC 5m Bot
set -e

cd "$(dirname "$0")"

echo "============================================"
echo "  Polymarket BTC 5m Consensus Trading Bot"
echo "============================================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 not found. Please install Python 3.9+"
    exit 1
fi

# Install dependencies
echo "📦 Installing dependencies..."
pip install -q -r requirements.txt

# Check .env
if [ ! -f .env ]; then
    echo "❌ .env not found!"
    exit 1
fi

if ! grep -q "PRIVATE_KEY=." .env; then
    echo "⚠️  PRIVATE_KEY not set — running in DRY RUN mode only"
    echo "   Edit .env to add your private key for live trading"
fi

# Run
echo ""
echo "🚀 Starting bot..."
python3 bot.py "$@"
