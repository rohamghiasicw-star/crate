"""Cover art, proven without the engine.

Runs _cand_art / _cand_row / _run_search's parser over hand-built candidate dicts. No
network, no Shazam, no lookup. `/usr/bin/python3 test_cand_art.py` from engine/.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server as S
import crate_engine as E

CASES = [
    ("youtube watch",      {"source": "youtube", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}),
    ("youtube + params",   {"source": "youtube", "url": "https://www.youtube.com/watch?v=kJQP7kiw5Fk&t=42s"}),
    ("youtu.be short",     {"source": "youtube", "url": "https://youtu.be/5qap5aO4i9A"}),
    ("youtube /shorts/",   {"source": "youtube", "url": "https://www.youtube.com/shorts/jNQXAC9IVRw"}),
    ("youtube /embed/",    {"source": "youtube", "url": "https://www.youtube.com/embed/dQw4w9WgXcQ"}),
    ("youtube, signed thumb ignored",
                           {"source": "youtube", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                            "thumb": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hq720.jpg?sqp=-oaymwE&rs=AOn4"}),
    ("youtube, unparseable url",
                           {"source": "youtube", "url": "https://www.youtube.com/channel/UCabc"}),
    ("soundcloud -original",
                           {"source": "soundcloud", "url": "https://soundcloud.com/x/bass-boosted",
                            "thumb": "https://i1.sndcdn.com/artworks-6Z1pUWtcWMNb08qy-vq1zCQ-original.jpg"}),
    ("soundcloud -t500x500",
                           {"source": "soundcloud", "url": "https://soundcloud.com/x/y",
                            "thumb": "https://i1.sndcdn.com/artworks-abc-Def123-t500x500.jpg"}),
    ("soundcloud uploader avatar (no track art)",
                           {"source": "soundcloud", "url": "https://soundcloud.com/x/z",
                            "thumb": "https://i1.sndcdn.com/avatars-000123456789-abcdef-original.jpg"}),
    ("soundcloud, no thumb (ddg / google lane)",
                           {"source": "soundcloud", "url": "https://soundcloud.com/x/no-meta"}),
    ("soundcloud, literal NA leaked",
                           {"source": "soundcloud", "url": "https://soundcloud.com/x/na", "thumb": "NA"}),
    ("audiomack (no free thumbnail path)",
                           {"source": "audiomack", "url": "https://audiomack.com/x/song/y"}),
    ("unknown source, https thumb passes through",
                           {"source": "", "url": "https://example.com/t", "thumb": "https://cdn.example.com/a.jpg"}),
    ("garbage thumb",      {"source": "soundcloud", "url": "https://soundcloud.com/x/g", "thumb": 12345}),
    ("empty dict",         {}),
]

print("== _cand_art ==")
for name, c in CASES:
    print("%-44s -> %s" % (name, S._cand_art(c)))

print("\n== crate_engine._thumb ==")
for v in ["NA", "", None, "  ", "https://i1.sndcdn.com/artworks-a-b-original.jpg"]:
    print("%-50r -> %r" % (v, E._thumb(v)))

print("\n== _run_search parse, one yt-dlp --print line each ==")
FMT_ROWS = [
    "Gucci Mane - Rumours (bass boosted)\tslowedvault\thttps://soundcloud.com/slowedvault/rumours\t181\t41233\t900\thttps://i1.sndcdn.com/artworks-Ab1-Cd2-original.jpg",
    "king von ft lil durk\tvonedits\thttps://www.youtube.com/watch?v=dQw4w9WgXcQ\t204\t9100\tNA\tNA",
]
parts_keys = ["title", "uploader", "url", "duration", "plays", "likes", "thumb"]
for line in FMT_ROWS:
    parts = line.split("\t")
    row = {"title": parts[0], "uploader": parts[1], "url": parts[2],
           "source": "soundcloud" if "soundcloud" in parts[2] else "youtube",
           "duration": parts[3], "plays": E._num(parts[4]), "likes": E._num(parts[5]),
           "query": "q", "thumb": E._thumb(parts[6]) if len(parts) > 6 else None}
    print({k: row.get(k) for k in parts_keys})
    print("   art ->", S._cand_art(row))

print("\n== full _cand_row on a realistic scored candidate ==")
cand = {"title": "Gucci Mane - Rumours (bass boosted)", "uploader": "slowedvault",
        "source": "soundcloud", "url": "https://soundcloud.com/slowedvault/rumours",
        "thumb": "https://i1.sndcdn.com/artworks-Ab1-Cd2-original.jpg",
        "final": 0.9812, "core": 0.9744, "plays": 41233, "bass_delta": -11.2,
        "slope_delta": -3.1, "vspeed": 0.7213, "vspeed_locked": 0.7200,
        "editmatch": True, "aligned_at": 0.0}
row = S._cand_row(cand)
for k in sorted(row):
    print("  %-16s %r" % (k, row[k]))

print("\n== same row with art stripped (old payload / no metadata lane) ==")
cand2 = dict(cand); cand2.pop("thumb")
print("  art ->", S._cand_row(cand2)["art"])

print("\n== ranking cannot see art: identical rows differing only in art ==")
a = dict(cand); b = dict(cand); b.pop("thumb")
ra, rb = S._cand_row(a), S._cand_row(b)
diff = {k for k in set(ra) | set(rb) if ra.get(k) != rb.get(k)}
print("  fields that differ:", diff)
assert diff == {"art"}, diff
print("  OK: art is the only field the thumbnail can move.")
