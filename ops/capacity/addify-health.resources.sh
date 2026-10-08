# PROPOSAL ONLY (CAPACITY 2026-10-08). A block for ~/addify-harness/server-kit/addify-health.sh,
# pasted right after the engine block (where "$H" holds the /health answer). ALERT ONLY: it
# never restarts anything, because a restart drops every scan in flight.
# Needs the engine change that adds server.proc to /health (server.py _proc_facts, branch
# call-capacity). Against an engine without it, every value reads empty and nothing fires.
#
# Thresholds (override in the health unit's environment):
#   ADDIFY_FD_ALERT          open fds in the engine            default 2000 (live today: 14)
#   ADDIFY_CLOSEWAIT_ALERT   CLOSE-WAIT sockets in the engine  default 100  (live today: 5;
#                            the FD leak fixed in 7125e63 reached 623 in 78 min)
#   ADDIFY_RSS_ALERT_MB      engine RSS                        default 4500 (live today: 600)
#   ADDIFY_MEMAVAIL_ALERT_MB box MemAvailable                  default 800  (live today: 6656)
FD_ALERT="${ADDIFY_FD_ALERT:-2000}"
CW_ALERT="${ADDIFY_CLOSEWAIT_ALERT:-100}"
RSS_ALERT="${ADDIFY_RSS_ALERT_MB:-4500}"
AVAIL_ALERT="${ADDIFY_MEMAVAIL_ALERT_MB:-800}"
if [ "$state" = "active" ] && [ -n "${H:-}" ]; then
  pr="$(echo "$H" | jq -c '.server.proc // {}' 2>/dev/null)"
  fds="$(echo "$pr" | jq -r '.fds // empty' 2>/dev/null)"
  cw="$(echo "$pr" | jq -r '.close_wait // empty' 2>/dev/null)"
  rss="$(echo "$pr" | jq -r '.rss_mb // empty | floor' 2>/dev/null)"
  avail="$(awk '/^MemAvailable:/{print int($2/1024)}' /proc/meminfo 2>/dev/null)"
  res_why=""
  [ -n "$fds" ] && [ "$fds" -ge "$FD_ALERT" ] && res_why="$res_why ${fds} open fds (alert at ${FD_ALERT});"
  [ -n "$cw" ] && [ "$cw" -ge "$CW_ALERT" ] && res_why="$res_why ${cw} CLOSE-WAIT sockets (alert at ${CW_ALERT}, the 7125e63 leak signature);"
  [ -n "$rss" ] && [ "$rss" -ge "$RSS_ALERT" ] && res_why="$res_why engine RSS ${rss} MB (alert at ${RSS_ALERT});"
  [ -n "$avail" ] && [ "$avail" -le "$AVAIL_ALERT" ] && res_why="$res_why box MemAvailable ${avail} MB (alert at ${AVAIL_ALERT});"
  if [ -n "$res_why" ]; then
    alert engine_resources fail "engine resources high:${res_why} Tried: nothing automatic (a restart drops scans in flight). Look at /health server.proc and journalctl -u addify-engine. Still high until a RECOVERED message follows."
  else
    alert engine_resources ok "engine fds, CLOSE-WAIT, RSS and box memory back under their alert lines"
  fi
fi
