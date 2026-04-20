#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

cleanup() {
  kill "${SERVER_PID:-}" "${DEV_PID:-}" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

npm run server &
SERVER_PID=$!

sleep 1

npm run dev -- --host &
DEV_PID=$!

wait "${SERVER_PID}" "${DEV_PID}"
