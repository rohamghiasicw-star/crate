"""LRCLIB lyric check for lyric search on the official Genius API (2026-11-17). Offline: LRCLIB is mocked.

Run: python test_lrclib.py   (from engine/, with the engine's venv)
"""
import os, sys
os.environ.setdefault("GENIUS_ACCESS_TOKEN", "test-token-not-real")
import server as S

FAILS = []


def check(name, ok, got=None):
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  got=%r" % (got,)))
    if not ok:
        FAILS.append(name)


LYR = ("Say you'll remember me\nStanding in a nice dress\nStaring at the sunset, babe\n"
       "Red lips and rosy cheeks\nSay you'll see me again")
calls = []


def fake_json(url, timeout):
    calls.append(url)
    if "api/get" in url and "Wildest%20Dreams" in url and "Taylor%20Swift" in url:
        return {"plainLyrics": LYR}
    if "api/search" in url and "Other%20Song" in url:
        return [{"trackName": "Other Song", "artistName": "Someone Else", "plainLyrics": "wrong artist words"},
                {"trackName": "Other Song", "artistName": "Real Artist", "plainLyrics": "say you'll remember me standing"}]
    return None


S._s_lrc_json = fake_json
S._S_LRC_CACHE.clear()

hits = [{"title": "Wildest Dreams", "artist": "Taylor Swift", "highlight": "", "frags": []},
        {"title": "Other Song", "artist": "Real Artist", "highlight": "", "frags": []},
        {"title": "Unknown", "artist": "Nobody", "highlight": "", "frags": []},
        {"title": "Has Excerpt", "artist": "Web Mode", "highlight": "kept excerpt", "frags": ["kept excerpt"]},
        {"title": "Fifth", "artist": "Past Top", "highlight": "", "frags": []}]
out = S._s_lrc_fill(hits)
check("fill returns a copy, input untouched", out is not hits and hits[0]["highlight"] == "")
check("top hit gets the song's lyrics", out[0]["highlight"] == LYR, out[0].get("highlight"))
check("search fallback picks the row with the same artist and title",
      out[1]["highlight"] == "say you'll remember me standing", out[1].get("highlight"))
check("no lyrics found leaves the hit as it was", out[2] is hits[2])
check("a hit that already has an excerpt is not looked up", out[3] is hits[3]
      and not any("Has%20Excerpt" in c for c in calls))
check("only the top %d hits are looked up" % S._S_LRC_TOP, out[4] is hits[4]
      and not any("Fifth" in c for c in calls))
check("only title and artist are sent, never the typed words",
      all("standing" not in c.lower() and "nice%20dress" not in c.lower() for c in calls), calls)

ptoks = S._s_toks("say you'll remember me standing in a nice dress")
check("typed words are found word for word in the filled lyrics",
      S._s_phrase_hit(ptoks, out[0]["highlight"], out[0].get("frags")) == "exact")
check("words that are not in the song are not confirmed",
      S._s_phrase_hit(S._s_toks("we never go out of style tonight"), out[0]["highlight"], []) is None)

n = len(calls)
S._s_lrc_fill(hits)
check("a second search uses the cache (misses too)", len(calls) == n, len(calls) - n)

S._S_LRC_CACHE.clear(); calls.clear()
S._s_lrc_json = lambda url, timeout: (_ for _ in ()).throw(OSError("down"))
try:
    out2 = S._s_lrc_fill(hits[:1])
    check("LRCLIB down: the hit stays as it was, no exception", out2[0] is hits[0])
except Exception as e:
    check("LRCLIB down: no exception", False, repr(e))

print("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS))
sys.exit(1 if FAILS else 0)
