#!/bin/bash
# Crate - double-click to start. Runs the engine on your Mac, opens the app,
# and (if a tunnel is installed) prints a public link you can send to anyone.
cd "$(dirname "$0")" || exit 1
PY=/usr/bin/python3
PORT=8788

echo ""
echo "  Crate - starting up..."

# stop any old engine on the port
lsof -ti tcp:$PORT 2>/dev/null | xargs kill -9 2>/dev/null

# start the engine (serves the app AND does the listening, one origin)
"$PY" server.py > crate_engine.log 2>&1 &
ENGINE_PID=$!

# wait until it answers
UP=""
for i in $(seq 1 30); do
  if curl -s "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then UP=1; break; fi
  sleep 0.5
done
if [ -z "$UP" ]; then
  echo "  Engine didn't start. See crate_engine.log:"
  tail -5 crate_engine.log
  exit 1
fi

# open it in the browser for you
open "http://127.0.0.1:$PORT/"

# public link for sharing (free tunnel, no account) - only if cloudflared exists
PUBURL=""; CF_PID=""
if command -v cloudflared >/dev/null 2>&1; then
  cloudflared tunnel --url "http://127.0.0.1:$PORT" > crate_tunnel.log 2>&1 &
  CF_PID=$!
  for i in $(seq 1 40); do
    PUBURL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' crate_tunnel.log | head -1)
    [ -n "$PUBURL" ] && break
    sleep 1
  done
fi

echo ""
echo "  ============================================================"
echo "   CRATE IS RUNNING"
echo ""
echo "   On this Mac:   http://127.0.0.1:$PORT/"
if [ -n "$PUBURL" ]; then
  echo "   Share link:    $PUBURL"
  echo "                  (send it to anyone - works on their phone too)"
else
  echo "   Share link:    not set up (run: brew install cloudflared)"
fi
echo ""
echo "   Keep this window open. Close it (or Ctrl-C) to stop Crate."
echo "  ============================================================"
echo ""

trap 'echo "  stopping..."; kill $ENGINE_PID $CF_PID 2>/dev/null; exit 0' INT TERM
wait $ENGINE_PID
