#!/usr/bin/env bash
set -euo pipefail

# Change this to use a different port (default 5000)
APP_PORT="${APP_PORT:-5000}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

echo "==> Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Starting app with pm2 (port $APP_PORT)..."
export PORT="$APP_PORT"
pm2 delete pdf-converter-bkt:5000 2>/dev/null || true
pm2 start app.py --name pdf-converter-bkt:5000 --interpreter .venv/bin/python
pm2 save

echo "==> Done. pdf-converter-bkt:5000 running under pm2."
pm2 status
