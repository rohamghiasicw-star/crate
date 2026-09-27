#!/usr/bin/env bash
# Addify one-server install. Ubuntu 24.04 x86_64, run as root, from the kit folder:
#   cd /opt/addify/kit && bash install.sh
# deploy.sh (on the Mac) copies the kit here and runs this for you. Safe to run again: every
# step checks before it changes anything, and a rerun only restarts what changed.
#
# What it sets up (README.md has the plain-words version):
#   apt packages, gh (GitHub's apt repo), cloudflared (Cloudflare's apt repo), user "addify",
#   the public engine repo at a pinned commit + server.diff, a venv from requirements.lock
#   (every wheel hash-checked), /etc/addify/*.env, systemd units (engine, tunnel, health
#   timer), journald limits, unattended security upgrades, a swap file, ufw (SSH only).
# Gist publishing starts DISABLED: the server never touches the gist until cutover.sh.
set -euo pipefail

KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL="https://github.com/rohamghiasicw-star/crate.git"
REPO_COMMIT="${ADDIFY_COMMIT:-1ea3836}"
APP=/opt/addify/app
VENV=/opt/addify/venv
ETC=/etc/addify
STATE=/var/lib/addify
LOGD=/var/log/addify
TMPD=/var/tmp/addify
SWAP_GB="${ADDIFY_SWAP_GB:-4}"
export DEBIAN_FRONTEND=noninteractive

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
skip() { printf '   skipped: %s\n' "$*"; }
die()  { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

# systemd is PID 1 on the droplet. In a test chroot it is not: then units are written but not
# started, and ufw/swap are left alone. Everything else runs the same.
HAVE_SYSTEMD=0
[ -d /run/systemd/system ] && HAVE_SYSTEMD=1

say "0. preflight"
[ "$(id -u)" = "0" ] || die "run as root"
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] && [ "${VERSION_ID:-}" = "24.04" ] || die "needs Ubuntu 24.04 (found ${PRETTY_NAME:-unknown})"
[ "$(uname -m)" = "x86_64" ] || die "needs x86_64 (found $(uname -m))"
for f in server.diff requirements.lock addify-tunnel.sh addify-health.sh gh-login.sh \
         systemd/addify-engine.service systemd/addify-tunnel.service \
         systemd/addify-health.service systemd/addify-health.timer \
         engine.env.default tunnel.env.default tools/parity_synth.py; do
  [ -f "$KIT/$f" ] || die "kit file missing: $f"
done
ok "Ubuntu 24.04 x86_64, systemd=$HAVE_SYSTEMD, kit at $KIT"

say "1. apt packages"
apt_marker=/var/lib/addify-apt-updated
if [ ! -f "$apt_marker" ] || [ -n "$(find "$apt_marker" -mmin +360 2>/dev/null)" ]; then
  apt-get update -q
  mkdir -p "$(dirname "$apt_marker")"; touch "$apt_marker"
fi
apt-get install -y -q --no-install-recommends \
  ca-certificates curl gnupg git jq python3 python3-venv ffmpeg libchromaprint-tools \
  sudo openssh-client unattended-upgrades ufw logrotate procps lsof tzdata >/dev/null
ok "python $(python3 -V 2>&1 | cut -d' ' -f2), $(ffmpeg -version | head -1 | cut -d' ' -f1-3), fpcalc $(fpcalc -version | awk '{print $3}')"

say "2. gh (GitHub's apt repo) and cloudflared (Cloudflare's apt repo)"
mkdir -p -m 755 /etc/apt/keyrings /usr/share/keyrings
added=0
if [ ! -s /etc/apt/keyrings/githubcli-archive-keyring.gpg ]; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  added=1
fi
GH_LIST="deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main"
if [ "$(cat /etc/apt/sources.list.d/github-cli.list 2>/dev/null)" != "$GH_LIST" ]; then
  echo "$GH_LIST" > /etc/apt/sources.list.d/github-cli.list; added=1
fi
if [ ! -s /usr/share/keyrings/cloudflare-public-v2.gpg ]; then
  curl -fsSL https://pkg.cloudflare.com/cloudflare-public-v2.gpg \
    -o /usr/share/keyrings/cloudflare-public-v2.gpg
  added=1
fi
CF_LIST="deb [signed-by=/usr/share/keyrings/cloudflare-public-v2.gpg] https://pkg.cloudflare.com/cloudflared any main"
if [ "$(cat /etc/apt/sources.list.d/cloudflared.list 2>/dev/null)" != "$CF_LIST" ]; then
  echo "$CF_LIST" > /etc/apt/sources.list.d/cloudflared.list; added=1
fi
[ "$added" = "1" ] && apt-get update -q
apt-get install -y -q gh cloudflared >/dev/null
ok "$(gh --version | head -1), $(cloudflared --version 2>&1 | head -1 | cut -d' ' -f1-3)"

say "3. user addify and folders"
if ! id addify >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /home/addify --shell /usr/sbin/nologin addify
  ok "user addify created"
else
  ok "user addify exists"
fi
install -d -m 755 -o root -g root /opt/addify "$ETC"
install -d -m 750 -o addify -g addify "$STATE" "$STATE/cache" "$STATE/data" "$STATE/tunnel" \
  "$STATE/xdg-cache" "$STATE/deno" "$LOGD" "$TMPD"
ok "/opt/addify, $ETC, $STATE, $LOGD, $TMPD"

say "4. engine code: $REPO_URL at $REPO_COMMIT + server.diff"
if [ ! -d "$APP/.git" ]; then
  git clone -q "$REPO_URL" "$APP"
fi
git config --global --get-all safe.directory 2>/dev/null | grep -qx "$APP" \
  || git config --global --add safe.directory "$APP"
cd "$APP"
if ! git cat-file -e "${REPO_COMMIT}^{commit}" 2>/dev/null; then
  git fetch -q origin
fi
DIFF_SHA="$(sha256sum "$KIT/server.diff" | cut -c1-16)"
APPLIED="$(cat "$APP/.addify-applied" 2>/dev/null || true)"
if [ "$APPLIED" != "$REPO_COMMIT $DIFF_SHA" ]; then
  git reset -q --hard "$REPO_COMMIT"
  git clean -q -fd engine
  git apply --check --directory=engine "$KIT/server.diff" || die "server.diff does not apply to $REPO_COMMIT"
  git apply --directory=engine "$KIT/server.diff"
  echo "$REPO_COMMIT $DIFF_SHA" > "$APP/.addify-applied"
  CODE_CHANGED=1
  ok "checked out $(git rev-parse --short HEAD), server.diff applied ($DIFF_SHA)"
else
  CODE_CHANGED=0
  ok "already at $REPO_COMMIT with this server.diff ($DIFF_SHA)"
fi
# The repo ships shazam_backend.txt = shazamkit (the Mac). The env below wins; this is a
# belt: with no env, a Linux engine must never try ShazamKit.
echo shazamio > "$APP/engine/shazam_backend.txt"
chown -R root:root "$APP"
chmod -R go-w "$APP"
cd /

say "5. python venv from the hash-pinned lock"
LOCK_SHA="$(sha256sum "$KIT/requirements.lock" | cut -c1-16)"
if [ "$(cat "$VENV/.addify-lock" 2>/dev/null || true)" != "$LOCK_SHA" ]; then
  rm -rf "$VENV"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --disable-pip-version-check --no-deps --require-hashes \
    -r "$KIT/requirements.lock"
  echo "$LOCK_SHA" > "$VENV/.addify-lock"
  VENV_CHANGED=1
  ok "venv built ($LOCK_SHA)"
else
  VENV_CHANGED=0
  ok "venv already matches the lock ($LOCK_SHA)"
fi
"$VENV/bin/python" -c "import numpy, shazamio, shazamio_core, curl_cffi, yt_dlp, audioop; print('   imports ok, numpy', numpy.__version__, 'yt-dlp', yt_dlp.version.__version__)"
[ -x "$VENV/bin/deno" ] && ok "deno $("$VENV/bin/deno" --version | head -1 | cut -d' ' -f2) (yt-dlp's JS runtime, as on the Mac)"

say "6. config: $ETC/engine.env and $ETC/tunnel.env"
# engine.env / tunnel.env come from the kit and are rewritten on every run, so a new kit's
# settings always land. Your own changes go in engine.local.env / tunnel.local.env: systemd
# reads those second, so a line there wins, and install.sh never touches them.
ENV_CHANGED=0
for e in engine tunnel; do
  if ! cmp -s "$KIT/$e.env.default" "$ETC/$e.env"; then
    install -m 644 "$KIT/$e.env.default" "$ETC/$e.env"; ENV_CHANGED=1
  fi
  [ -f "$ETC/$e.local.env" ] || printf '# your overrides for %s.env (KEY=value lines); install.sh never touches this file\n' "$e" > "$ETC/$e.local.env"
done
rm -f "$ETC/engine.env.default" "$ETC/tunnel.env.default"
ok "engine.env and tunnel.env from the kit; overrides in engine.local.env ($(grep -c '^[A-Z]' "$ETC/engine.local.env") set)"

say "7. scripts and systemd units"
install -d -m 755 /opt/addify/bin
for s in addify-tunnel.sh addify-health.sh gh-login.sh; do
  install -m 755 "$KIT/$s" "/opt/addify/bin/$s"
done
install -m 644 "$KIT/tools/parity_synth.py" /opt/addify/bin/parity_synth.py
[ -f "$KIT/tools/shazam_limit.py" ] && install -m 644 "$KIT/tools/shazam_limit.py" /opt/addify/bin/shazam_limit.py
UNITS_CHANGED=0
for u in addify-engine.service addify-tunnel.service addify-health.service addify-health.timer; do
  if ! cmp -s "$KIT/systemd/$u" "/etc/systemd/system/$u"; then
    install -m 644 "$KIT/systemd/$u" "/etc/systemd/system/$u"; UNITS_CHANGED=1
  fi
done
cat > /etc/logrotate.d/addify <<'EOF'
/var/log/addify/*.jsonl /var/log/addify/*.log {
  daily
  rotate 7
  maxsize 100M
  compress
  missingok
  notifempty
  su addify addify
  create 0640 addify addify
}
EOF
ok "units in /etc/systemd/system, logrotate for $LOGD"

say "8. journald limits, unattended security upgrades"
install -d -m 755 /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/addify.conf <<'EOF'
[Journal]
SystemMaxUse=1G
SystemKeepFree=2G
MaxRetentionSec=14day
EOF
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
cat > /etc/apt/apt.conf.d/52addify-unattended <<'EOF'
// security updates only (Ubuntu's default origins), no automatic reboot: a reboot is yours
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
EOF
ok "journald capped at 1G / 14 days, security updates daily, no auto reboot"

say "9. swap file (${SWAP_GB}G)"
if [ "$HAVE_SYSTEMD" = "1" ]; then
  if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
    [ -f /swapfile ] || { fallocate -l "${SWAP_GB}G" /swapfile; chmod 600 /swapfile; mkswap /swapfile >/dev/null; }
    swapon /swapfile
  fi
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo 'vm.swappiness=10' > /etc/sysctl.d/60-addify.conf
  sysctl -q -p /etc/sysctl.d/60-addify.conf || true
  ok "$(swapon --show=NAME,SIZE --noheadings | tr '\n' ' ')"
else
  skip "no systemd (test chroot): swap left to the host"
fi

say "10. firewall: SSH in, nothing else (the tunnel is outbound)"
if [ "$HAVE_SYSTEMD" = "1" ]; then
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow OpenSSH >/dev/null
  ufw --force enable >/dev/null
  ok "$(ufw status | head -1)"
else
  skip "no systemd (test chroot): ufw not enabled"
fi

say "11. start"
if [ "$HAVE_SYSTEMD" = "1" ]; then
  systemctl daemon-reload
  systemctl restart systemd-journald || true
  systemctl enable -q addify-engine.service addify-tunnel.service addify-health.timer
  if [ "$CODE_CHANGED$VENV_CHANGED$UNITS_CHANGED$ENV_CHANGED" != "0000" ] || ! systemctl -q is-active addify-engine; then
    systemctl restart addify-engine.service       # drains running scans first (up to 110 s)
  fi
  systemctl start addify-health.timer
  for i in $(seq 1 60); do
    curl -s -m 3 http://127.0.0.1:8788/health | grep -q '"crate engine"' && break
    sleep 1
  done
  curl -s -m 5 http://127.0.0.1:8788/health | grep -q '"crate engine"' || die "engine not answering /health on 127.0.0.1:8788 (journalctl -u addify-engine)"
  ok "engine answers /health on 127.0.0.1:8788"
  if [ "$UNITS_CHANGED$ENV_CHANGED" != "00" ] || ! systemctl -q is-active addify-tunnel; then
    systemctl restart addify-tunnel.service
  fi
  ok "tunnel service $(systemctl is-active addify-tunnel)"
else
  skip "no systemd (test chroot): units written, not started"
fi

say "12. audio parity self-test (synthetic audio, nothing kept)"
PAR="$(sudo -u addify env TMPDIR="$TMPD" "$VENV/bin/python" /opt/addify/bin/parity_synth.py "$APP/engine" --line 2>/dev/null || true)"
echo "   $PAR"
echo "$PAR" | grep -q '"fp_md5": "6b0484ed1fac"' && ok "fingerprint matches the Mac (6b0484ed1fac)" \
  || echo "   WARNING: fingerprint differs from the Mac's 6b0484ed1fac (scores may shift a little)"

say "done"
echo "   Gist publishing is OFF until cutover. Next: gh-login.sh (one time), check.sh, cutover.sh."
