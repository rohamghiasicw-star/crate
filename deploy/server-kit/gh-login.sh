#!/usr/bin/env bash
# One-time GitHub login for the server's gist publishing, by GitHub's device flow.
#   From the Mac:   ./gh-login.sh <server IP>
#   On the server:  /opt/addify/bin/gh-login.sh
# It prints a one-time code and https://github.com/login/device . Open that page in your
# browser (logged in as rohamghiasicw-star), type the code, approve. The token is created by
# GitHub and stored by gh on the server only (~addify/.config/gh); it never passes through a
# file in this kit or through chat. Scope asked for: gist (gh adds its own defaults).
set -euo pipefail
if [ $# -ge 1 ] && [ -n "$1" ]; then
  IP="$1"
  exec ssh -t -i "${ADDIFY_SSH_KEY:-$HOME/.ssh/addify_server}" -o StrictHostKeyChecking=accept-new \
    "root@$IP" /opt/addify/bin/gh-login.sh
fi
[ "$(id -u)" = "0" ] || { echo "run as root on the server (it switches to user addify)"; exit 1; }
if sudo -u addify -H gh auth status -h github.com >/dev/null 2>&1; then
  echo "gh is already logged in for user addify:"
  sudo -u addify -H gh auth status -h github.com 2>&1 | sed 's/^/   /' | grep -v -i token || true
  exit 0
fi
echo "Starting GitHub device login for user addify."
echo "It prints a one-time code (XXXX-XXXX). On your Mac open https://github.com/login/device,"
echo "type the code, approve. This window then says it is logged in. Ignore any clipboard warning."
echo
# GH_PROMPT_DISABLED: no questions, just the code and the URL (tested on gh 2.101.0)
sudo -u addify -H env GH_PROMPT_DISABLED=1 GH_BROWSER=true BROWSER=true \
  gh auth login --hostname github.com --git-protocol https --web -s gist
echo
sudo -u addify -H gh auth status -h github.com 2>&1 | grep -v -i token | sed 's/^/   /'
echo "Done. The tunnel service publishes to the gist only after cutover.sh."
