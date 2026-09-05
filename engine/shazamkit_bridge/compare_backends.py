#!/usr/bin/env /usr/bin/python3
"""Run the same WAVs through shazamio and the ShazamKit bridge, side by side.

    /usr/bin/python3 compare_backends.py a.wav b.wav ...        # health probe + compare
    /usr/bin/python3 compare_backends.py --probes 3 a.wav       # shorter health probe

Refuses to compare until the hard-rules health probe passes (6 spaced shazamio calls on
a known-good clip, each expected ~0.5 s). A throttled backend answers the first probe and
stalls the rest, and every number measured under it is fiction - hard-rules.md, 2026-08-12.

Calls are strictly sequential and 3 s apart. Shazam rate-limits on concurrency, and the
live engine on 8788 shares the quota; never run this while the tester is on.

On an unentitled Mac the shazamkit column reads "error com.apple.ShazamCore/102" on
every row. That IS the result here and it is printed, not hidden. The comparison this
script exists for (title/key agreement, freqskew sign, offset, latency) only means
something on a build signed with a provisioning profile - see README.md.

Lives in shazamkit_bridge/, not testruns/: testruns/ is gitignored (2 GB of audio scratch)
and this has to travel with the branch.
"""
import asyncio, importlib, os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)

HEALTH_CLIP = "https://vt.tiktok.com/ZSXWjGrqT/"   # the hard-rules probe clip
GAP = 3.0


def load(backend):
    """find_song reads CRATE_SHAZAM_BACKEND at import, so swap the env and re-import."""
    os.environ["CRATE_SHAZAM_BACKEND"] = backend
    sys.modules.pop("find_song", None)
    return importlib.import_module("find_song")


async def call(fs, wav):
    t0 = time.time()
    try:
        hit = await asyncio.wait_for(fs.shazam(wav), timeout=12)
        return hit, time.time() - t0, None
    except asyncio.TimeoutError:
        return None, time.time() - t0, "TIMEOUT"
    except Exception as e:  # the bridge raises on error-not-nomatch; show it, do not hide it
        return None, time.time() - t0, str(e)[:120]


async def health(n):
    fs = load("shazamio")
    import crate_engine as E
    src = E.get_source(HEALTH_CLIP)
    t = tempfile.mkdtemp()
    ok = 0
    print("health probe (shazamio, %d spaced calls, expect ~0.5 s each)" % n)
    try:
        for i in range(n):
            w = os.path.join(t, "p%d.wav" % i)
            fs.cut(src["audio"], w, 0.0, 1.00, span=12)
            hit, dt, err = await call(fs, w)
            print("  probe %d: %.2fs %s" % (i + 1, dt, err or ("hit" if hit else "no hit")))
            ok += bool(hit)
            if i + 1 < n:
                await asyncio.sleep(GAP)
    finally:
        # never leave audio behind - retention doctrine
        for f in os.listdir(t):
            os.remove(os.path.join(t, f))
        os.rmdir(t)
    return ok == n


def cell(hit, err):
    if err:
        return ("error", "", "", "", "")
    if not hit:
        return ("no match", "", "", "", "")
    return (hit.get("title") or "", hit.get("artist") or "", str(hit.get("key") or ""),
            "%s" % hit.get("freqskew"), "%s" % hit.get("offset_in_master", ""))


async def main(wavs, probes):
    if not await health(probes):
        print("HEALTH PROBE FAILED: backend is throttled or down. Nothing measured now is real. Stop.")
        return 1
    rows = []
    for wav in wavs:
        await asyncio.sleep(GAP)
        a, ta, ea = await call(load("shazamio"), wav)
        await asyncio.sleep(GAP)
        b, tb, eb = await call(load("shazamkit"), wav)
        agree = "Y" if (a and b and str(a.get("key")) == str(b.get("key"))) else "N"
        rows.append((os.path.basename(wav), cell(a, ea), ta, ea, cell(b, eb), tb, eb, agree))
    print()
    print("%-22s %-9s %-11s %-30s %-24s %-11s %-9s %-8s %s" % (
        "wav", "backend", "latency", "title", "artist", "key", "freqskew", "offset", "note"))
    for name, ca, ta, ea, cb, tb, eb, agree in rows:
        print("%-22s %-9s %-11s %-30s %-24s %-11s %-9s %-8s %s" % (
            name, "shazamio", "%.2fs" % ta, ca[0][:30], ca[1][:24], ca[2], ca[3], ca[4], ea or ""))
        print("%-22s %-9s %-11s %-30s %-24s %-11s %-9s %-8s %s" % (
            "", "shazamkit", "%.2fs" % tb, cb[0][:30], cb[1][:24], cb[2], cb[3], cb[4], eb or ""))
        print("%-22s agree(key)=%s" % ("", agree))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    probes = 6
    if args and args[0] == "--probes":
        probes = int(args[1]); args = args[2:]
    if not args:
        print(__doc__); sys.exit(2)
    sys.exit(asyncio.get_event_loop().run_until_complete(main(args, probes)))
