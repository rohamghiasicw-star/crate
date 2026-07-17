#!/usr/bin/env python3
"""Score the engine on ORIGINAL SOUNDS ONLY.

A video whose credit already names a track is a giveaway: the free tier answers
it without the engine. Including those inflates the score, so this harness
re-checks every link's credit itself and DROPS any that names a track. The
number it prints is only ever the hard case.

Usage: python3 test_original_only.py urls.json [concurrency]
"""
import asyncio, json, os, re, sys, tempfile, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from find_song import (fetch, resolve, scrape_music, duration_of, windows_for,
                       cut, shazam, UA)

ORIGINAL_WORDS = [
    "original sound", "som original", "sonido original", "son original",
    "originalton", "suono originale", "origineel geluid", "oryginalny dźwięk",
    "sunet original", "оригинальный звук", "оригінальний звук", "orijinal ses",
    "âm thanh gốc", "suara asli", "เสียงต้นฉบับ", "オリジナル楽曲", "オリジナル曲",
    "오리지널 사운드", "原聲", "原声", "原创音乐", "原創音樂", "الصوت الأصلي",
    "मूल ध्वनि", "orihinal na tunog", "původní zvuk", "originalljud",
    "alkuperäinen ääni", "original lyd", "eredeti hang", "αρχικός ήχος",
    "צליל מקורי", "оригинален звук", "sunetul original",
]
PASSES = [(1.00, "as posted"), (0.80, "sped ~1.25x"), (0.77, "sped ~1.30x"),
          (0.85, "sped ~1.18x"), (1.25, "slowed ~0.8x")]


def is_original(c):
    """The marker isn't always first: Turkish gives "dexterc7 - orijinal ses",
    and credits arrive doubled ("original sound - Tricky - Tricky"). Test every
    segment, or real originals slip through as named tracks."""
    if not c:
        return True
    segs = re.split(r"\s+[-–]\s+", c.strip().lower())
    return any(seg.strip() in ORIGINAL_WORDS for seg in segs)


def credit_via_oembed(url):
    """Independently re-check the credit. Never trust the harvest's label."""
    api = "https://www.tiktok.com/oembed?url=" + urllib.parse.quote(url.split("?")[0], safe="")
    req = urllib.request.Request(api, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        j = json.loads(r.read().decode("utf-8", "replace"))
    if not j.get("title") and not j.get("author_name"):
        return None, "soft-fail (removed/private)"
    m = re.search(r">\s*♬\s*([^<]*)<", j.get("html") or "")
    return (m.group(1).strip() if m else None), None


async def run_one(rec, sem, stats):
    url = rec["url"] if isinstance(rec, dict) else rec
    async with sem:
        tag = url.split("@")[1].split("/")[0] if "@" in url else url[-12:]
        # gate first: only original sounds count
        try:
            credit, err = credit_via_oembed(url)
        except Exception as e:
            stats["dead"] += 1
            return None
        if err:
            stats["dead"] += 1
            return None
        if not is_original(credit):
            stats["giveaway"] += 1          # credit names the track: not our job
            return None
        stats["eligible"] += 1

        tmp = tempfile.mkdtemp()
        t0 = time.time()
        try:
            info = None
            for a in range(3):
                try:
                    info = scrape_music(resolve(url)); break
                except Exception:
                    await asyncio.sleep(5 + a * 5)
            if not info or not info.get("playUrl"):
                return {"tag": tag, "credit": credit, "song": None, "why": "scrape blocked"}
            raw = os.path.join(tmp, "a.mp3")
            # one slow CDN read must not take the whole batch down with it
            got = False
            for a in range(3):
                try:
                    open(raw, "wb").write(fetch(info["playUrl"], binary=True, timeout=45))
                    got = True
                    break
                except Exception:
                    await asyncio.sleep(3 + a * 3)
            if not got:
                return {"tag": tag, "credit": credit, "song": None, "why": "audio fetch timeout"}
            offs = windows_for(duration_of(raw))
            for rate, label in PASSES:
                for off in offs:
                    wav = os.path.join(tmp, "w.wav")
                    try:
                        cut(raw, wav, off, rate)
                        hit = await shazam(wav)
                    except Exception:
                        continue
                    finally:
                        if os.path.exists(wav):
                            os.remove(wav)
                    if hit:
                        return {"tag": tag, "credit": credit,
                                "song": "%s - %s" % (hit["title"], hit["artist"]),
                                "how": "%s @%.0fs" % (label, off),
                                "secs": round(time.time() - t0, 1)}
            return {"tag": tag, "credit": credit, "song": None, "why": "no match",
                    "secs": round(time.time() - t0, 1)}
        except Exception as e:
            return {"tag": tag, "credit": credit, "song": None,
                    "why": "error: " + str(e)[:34]}
        finally:
            for f in os.listdir(tmp):
                try: os.remove(os.path.join(tmp, f))
                except Exception: pass


async def main():
    src = sys.argv[1]
    conc = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    urls = json.load(open(src, encoding="utf-8"))
    stats = {"eligible": 0, "giveaway": 0, "dead": 0}
    sem = asyncio.Semaphore(conc)
    t0 = time.time()
    rs = [r for r in await asyncio.gather(*[run_one(u, sem, stats) for u in urls]) if r]

    found = [r for r in rs if r["song"]]
    print("\n%-17s %-28s %s" % ("creator", "credit (no track name)", "REAL SONG"))
    print("-" * 104)
    for r in sorted(rs, key=lambda x: x["song"] is None):
        print("%-17s %-28s %s" % (r["tag"][:17], (r["credit"] or "")[:28],
                                  r["song"] or ("-- " + r.get("why", "no match") + " --")))
    print("-" * 104)
    print("input links      : %d" % len(urls))
    print("  dropped, dead  : %d" % stats["dead"])
    print("  dropped, credit already named the track (giveaway): %d" % stats["giveaway"])
    print("  ELIGIBLE (original sound, the hard case)         : %d" % stats["eligible"])
    if rs:
        print("\nNAMED THE SONG   : %d / %d  = %.0f%%   in %.0fs"
              % (len(found), len(rs), 100 * len(found) / len(rs), time.time() - t0))
        if found:
            secs = sorted(r.get("secs", 0) for r in found)
            print("time per hit     : median %.0fs" % secs[len(secs) // 2])
    json.dump(rs, open("original_only_results.json", "w"), indent=1, ensure_ascii=False)

if __name__ == "__main__":
    asyncio.run(main())
