#!/usr/bin/env bash
# PROMISE GATE: run the promise ledger (engine/test_promise_ledger.py) on the page a deploy is
# about to ship, on this Mac, headless, against a mocked engine. Nothing touches the server.
#
#   ops/promise-gate.sh <ref>                 the ledger of <ref> against the page of <ref>
#   ops/promise-gate.sh <ref> <live-ref>      ...and the RATCHET: the ledger of the release that runs
#                                             today against the new page, so a commit cannot ship by
#                                             deleting the check it breaks
#
# exit 0 = every promise holds; 1 = a promise broke (the deploy must stop); 2 = the gate could not run.
# ADDIFY_REPO (default ~/crate-repo) must already hold <ref> (deploy-head.guarded.sh fetches first).
# PROMISE_LEDGER: the ledger to use when <ref> predates it (default: the one beside this script).
set -euo pipefail
REPO="${ADDIFY_REPO:-$HOME/crate-repo}"
REF="${1:?usage: promise-gate.sh <ref> [<live-ref>]}"
LIVE="${2:-}"
PY="${PROMISE_PY:-/usr/bin/python3}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FALLBACK="${PROMISE_LEDGER:-$HERE/../engine/test_promise_ledger.py}"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT

git -C "$REPO" cat-file -e "${REF}^{commit}" 2>/dev/null || { echo "promise gate: $REF is not in $REPO"; exit 2; }
SHA="$(git -C "$REPO" rev-parse --short "$REF")"
git -C "$REPO" show "${REF}:engine/crate.html" > "$T/crate.html"

if git -C "$REPO" show "${REF}:engine/test_promise_ledger.py" > "$T/ledger_ref.py" 2>/dev/null; then
  LEDGER="$T/ledger_ref.py"; WHICH="$SHA's own ledger"
else
  [ -f "$FALLBACK" ] || { echo "promise gate: $SHA has no ledger and $FALLBACK is missing"; exit 2; }
  LEDGER="$FALLBACK"; WHICH="fallback ledger $FALLBACK"
fi

rc=0
echo "-- promise ledger: $WHICH on $SHA's crate.html"
"$PY" "$LEDGER" --file "$T/crate.html" --json "$T/ref.json" || rc=$?

if [ -n "$LIVE" ]; then
  if git -C "$REPO" cat-file -e "${LIVE}^{commit}" 2>/dev/null \
     && git -C "$REPO" show "${LIVE}:engine/test_promise_ledger.py" > "$T/ledger_live.py" 2>/dev/null; then
    echo "-- ratchet: the live release's ledger ($LIVE) on $SHA's crate.html"
    "$PY" "$T/ledger_live.py" --file "$T/crate.html" --json "$T/live.json" || { r=$?; [ "$rc" -ge "$r" ] || rc=$r; }
  else
    echo "-- ratchet skipped: the live release ($LIVE) has no ledger yet"
  fi
fi

case "$rc" in
  0) echo "PROMISE GATE: PASS ($SHA)";;
  1) echo "PROMISE GATE: FAIL ($SHA) - a promise the owners were given is broken; see the FAIL lines above";;
  *) echo "PROMISE GATE: ERROR rc=$rc ($SHA) - the gate itself did not run";;
esac
exit "$rc"
