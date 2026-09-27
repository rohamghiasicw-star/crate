#!/usr/bin/env bash
# Linux port of the Mac watchdog's ensure_engine(). systemd already restarts a CRASHED engine
# (Restart=always); this catches a HUNG one: 3 failed /health checks in a row (30 s apart,
# 10 s timeout each) -> restart. A stop/restart in progress (draining) is left alone.
set -u
STRIKES=/run/addify-health.strikes
state="$(systemctl is-active addify-engine 2>/dev/null || true)"
if [ "$state" != "active" ]; then
  echo 0 > "$STRIKES"; exit 0
fi
if curl -s -m 10 http://127.0.0.1:8788/health | grep -q '"service": *"crate engine"'; then
  echo 0 > "$STRIKES"; exit 0
fi
n=$(( $(cat "$STRIKES" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STRIKES"
echo "engine /health failed, strike $n/3"
if [ "$n" -ge 3 ]; then
  echo "engine hung for 3 checks: restarting addify-engine"
  echo 0 > "$STRIKES"
  systemctl restart addify-engine
fi
