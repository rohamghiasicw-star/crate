"""Long-lived YouTube flat-search worker for crate_engine (speed3, 2026-09-26).

Runs under HOMEBREW's python - the same yt-dlp build `/opt/homebrew/bin/yt-dlp` runs, which
is the one YouTube search already uses (the py3.9 module is too old for YouTube, 09-25).
One interpreter for the whole server lifetime instead of one per search spec: a fresh
yt-dlp process costs ~1 s of start + import before a byte moves (SPEED-RESEARCH-SEARCH.md).

Protocol: JSON lines. In: {"id", "spec", "fmt"}. Out: {"id", "text", "err"} where `text` is
exactly what `yt-dlp <spec> --flat-playlist --print <fmt>` writes to stdout (one line per
entry), so crate_engine parses it with the same code as the subprocess path.
Exits when stdin closes (the engine went away)."""
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import yt_dlp

_tl = threading.local()
_out = threading.Lock()


def _ydl():
    # A FRESH YoutubeDL per search, like a fresh CLI process. A reused instance keeps
    # YouTube's session cookies between searches and measurably re-ranks the results
    # (lab parity run 2026-09-26: a persistent instance swapped rows and surfaced uploads
    # the CLI never returned on 3 of 4 queries). Construction is cheap; the interpreter
    # start and the import, which is what this worker saves, are paid once.
    return yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                             "extract_flat": "in_playlist"})


def _run(req):
    text, err = "", None
    y = None
    try:
        y = _ydl()
        info = y.extract_info(req["spec"], download=False)
        lines = []
        for e in (info or {}).get("entries") or []:
            if not e.get("webpage_url"):
                # the CLI fills this from `url` for flat entries (add_default_extra_info)
                e = dict(e, webpage_url=e.get("url"))
            lines.append(y.evaluate_outtmpl(req["fmt"], e))
        text = "\n".join(lines)
    except Exception as ex:                      # same as the CLI: no rows
        err = str(ex)[:200]
    finally:
        # FD LEAK (2026-10-06): an unclosed YoutubeDL stays reachable from the process-wide
        # 'urllib3' logger (yt-dlp's requests handler adds a handler holding it), so its
        # keep-alive YouTube socket never closes: this worker held 586 CLOSE-WAIT sockets on
        # live after 80 min. close() drops the handler and the pool; the rows are read already.
        if y is not None:
            try:
                y.close()
            except Exception:
                pass
    msg = json.dumps({"id": req["id"], "text": text, "err": err})
    with _out:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()


def main():
    ex = ThreadPoolExecutor(max_workers=32)
    sys.stdout.write(json.dumps({"id": "ready"}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if line:
            try:
                ex.submit(_run, json.loads(line))
            except Exception:
                pass


if __name__ == "__main__":
    main()
