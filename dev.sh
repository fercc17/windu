#!/usr/bin/env bash
# Launch windu on the LAN: Django API (0.0.0.0:8010) + Vite frontend
# (0.0.0.0:5173, proxies /api -> :8010).
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
PYBIN="${WINDU_PYTHON:-/home/fer/anaconda3/envs/cmdb/bin/python3.12}"
LANIP="$(hostname -I 2>/dev/null | awk '{print $1}')"

echo "windu — reachable on the LAN:"
echo "  frontend : http://${LANIP:-0.0.0.0}:5173"
echo "  API      : http://${LANIP:-0.0.0.0}:8010"
trap 'kill 0' EXIT
( cd "$ROOT/backend"  && "$PYBIN" manage.py runserver 0.0.0.0:8010 ) &
( cd "$ROOT/frontend" && npm run dev -- --host 0.0.0.0 ) &
wait
