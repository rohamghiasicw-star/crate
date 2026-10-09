"""Offline tests for trending_sounds.py. No network, no engine import: the live run on the
test box (2026-10-09, --max 30 --record) was recorded, scrubbed of creator names, and is
replayed here through ReplayNet. Synthetic cases cover the branches one real run cannot.
run: /usr/bin/python3 test_trending_sounds.py"""
import copy
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trending_sounds as S   # noqa: E402
import vidcache as VC         # noqa: E402

FIX = os.path.join(HERE, "fixtures", "trending_sounds", "recorded.json")


def _load():
    with open(FIX) as f:
        return json.load(f)


def _nosleep(_s):
    pass


# a sound id minted at unix time T has T in its top 32 bits; a video posted dt seconds
# later has T + dt there
def _sid(t, low=12345):
    return str((t << 32) + low)


T0 = 1790000000


class Classify(unittest.TestCase):
    def c(self, **kw):
        return S.classify(kw)

    def test_original_sound_labels_in_the_engines_languages(self):
        for t in ("original sound - sp3d_up", "original sound", "Originalton - abc",
                  "suono originale - Sounder", "son original - wtkh.edt7",
                  "sonido original - x", "som original - x", "nhạc nền - Ar.",
                  "оригинальный звук - x", "原声 - abc", "orijinal ses - x"):
            ok, rule, _ = self.c(sound_title=t)
            self.assertTrue(ok, t)
            self.assertEqual(rule, "title_original_sound", t)

    def test_edit_words(self):
        for t, w in (("Back to friends (sped up)", "sped up"), ("song - slowed + reverb", "slowed"),
                     ("X (Nightcore)", "nightcore"), ("BAS FUNK - SUPER SLOWED", "super slowed"),
                     ("Y mashup", "mashup"), ("Z bass boosted", "bass boosted"),
                     ("Dracula (JENNIE Remix)", "remix"), ("audio edit", "edit")):
            ok, rule, _ = self.c(sound_title=t)
            self.assertTrue(ok, t)
            self.assertEqual(rule, "title_edit_word:" + w, t)

    def test_styled_unicode_is_folded(self):
        self.assertTrue(self.c(sound_title="𝐬𝐥𝐨𝐰𝐞𝐝 song")[0])

    def test_no_false_edits(self):
        for t in ("Wonderwall (Remastered)", "Credit card", "Red Red Wine", "Iris",
                  "Editorial", "Speedway", "Reverberation Blues"):
            ok, rule, sig = self.c(sound_title=t)
            self.assertFalse(ok, t)
            self.assertEqual((rule, sig), ("no_original_signal", []), t)

    def test_song_hint_counts_for_edit_words(self):
        ok, rule, _ = self.c(sound_title="original song", song_hint="Golden Brown (slowed)")
        self.assertEqual((ok, rule), (True, "title_edit_word:slowed"))

    def test_tiktok_flag_and_tokchart_type(self):
        self.assertEqual(self.c(sound_title="AkpakO Bum Bum", tiktok_original=True)[:2],
                         (True, "tiktok_original_flag"))
        self.assertEqual(self.c(sound_title="Zany", tokchart_type="UGC Contains Music")[:2],
                         (True, "tokchart_type:UGC Contains Music"))
        self.assertEqual(self.c(sound_title="Red Red Wine", tokchart_type="Catalog Sound")[:2],
                         (False, "tokchart_type:Catalog Sound"))
        self.assertEqual(self.c(sound_title="Red Red Wine", tiktok_original=False)[:2],
                         (False, "no_original_signal"))

    def test_rule_order_title_first_all_signals_kept(self):
        ok, rule, sig = self.c(sound_title="original sound - x (sped up)", tiktok_original=True,
                               tokchart_type="UGC Contains Music")
        self.assertEqual(rule, "title_original_sound")
        self.assertEqual(sig, ["title_original_sound", "title_edit_word:sped up",
                               "tiktok_original_flag", "tokchart_type:UGC Contains Music"])


class Helpers(unittest.TestCase):
    def test_sound_id_from_url(self):
        self.assertEqual(S.sound_id_from_url("https://www.tiktok.com/music/x-7683032126865869601"),
                         "7683032126865869601")
        self.assertEqual(S.sound_id_from_url(
            "https://www.tiktok.com/music/original-sound-7684023257549261586?lang=en"),
            "7684023257549261586")
        self.assertIsNone(S.sound_id_from_url("https://music.apple.com/us/album/x/1?i=2"))

    def test_snowflake(self):
        self.assertEqual(S.snowflake_s("7586007061605141270"), 1766254906)  # creator_check.py
        self.assertIsNone(S.snowflake_s("nope"))

    def test_cloudflare_challenge(self):
        self.assertTrue(S.is_cf_challenge(403, "<!DOCTYPE html><title>Just a moment...</title>"))
        self.assertFalse(S.is_cf_challenge(200, '{"code":0}'))
        self.assertFalse(S.is_cf_challenge(403, "forbidden"))

    def test_walls_split_blocks_from_soft_503(self):
        req = {"tiktok_embed_v2": {"200": 9, "503": 4},
               "tikwm_feed": {"200": 2, "cloudflare_challenge": 1},
               "tikwm_challenge_posts": {"code-1:Free Api Limit: 1 request/se": 2},
               "tokchart": {"ok": 1}}
        self.assertEqual(S.walls(req), {"tiktok_embed_v2": {"soft_503": 4},
                                        "tikwm_feed": {"block": 1},
                                        "tikwm_challenge_posts": {"block": 2}})

    def test_parse_tikwm_items(self):
        items = [
            {"video_id": "1", "is_ad": True, "music_info": {"id": "9"}, "author": {"unique_id": "a"}},
            {"video_id": "2", "music_info": {"id": "0"}, "author": {"unique_id": "b"}},
            {"video_id": "3", "music_info": {"id": "77", "title": "original sound - c",
                                             "author": "C"},
             "author": {"unique_id": "c"}, "play_count": 5},
            {"video_id": "4", "images": ["u"], "music_info": {"id": "78", "title": "t"},
             "author": {"unique_id": "d"}},
        ]
        got = S.parse_tikwm_items(items)
        self.assertEqual([g["sound_id"] for g in got], ["77", "78"])
        self.assertEqual(got[0]["video"], {"id": "3", "author": "c", "plays": 5, "photo": False})
        self.assertTrue(got[1]["video"]["photo"])


class Pick(unittest.TestCase):
    def setUp(self):
        self.sid = _sid(T0)

    def tile(self, dt, who, plays, **kw):
        v = {"id": _sid(T0 + dt, 7), "authorUniqueId": who, "playCount": plays,
             "privateItem": False, "playAddr": "x"}
        v.update(kw)
        return v

    def test_origin_by_snowflake_wins_over_page_order(self):
        tiles = [self.tile(900000, "big", 5000000), self.tile(-4080, "owner", 900000),
                 self.tile(70000, "early", 2000000)]
        c = S.pick_candidates(self.sid, tiles, [])
        self.assertEqual((c[0]["author"], c[0]["rule"], c[0]["delta"]), ("owner", "origin_video", -4080))
        self.assertEqual([x["author"] for x in c], ["owner", "big", "early"])

    def test_no_origin_outside_window(self):
        tiles = [self.tile(S.ORIGIN_WINDOW_S + 1, "a", 30000)]
        self.assertEqual(S.pick_candidates(self.sid, tiles, [])[0]["rule"], "page_order_live")

    def test_private_dead_and_unplayable_tiles(self):
        tiles = [self.tile(5, "private", 10 ** 6, privateItem=True),
                 self.tile(6, "noaddr", 10 ** 6, playAddr=""),
                 self.tile(800000, "small", 100), self.tile(900000, "live", 50000)]
        c = S.pick_candidates(self.sid, tiles, [])
        self.assertEqual([(x["author"], x["rule"]) for x in c],
                         [("live", "page_order_live"), ("small", "page_order_any")])

    def test_source_video_is_last_and_photo_never(self):
        src = [{"id": _sid(T0 + 999999, 3), "author": "feed", "plays": 10 ** 8, "photo": False},
               {"id": _sid(T0 + 999998, 4), "author": "pic", "plays": 10 ** 9, "photo": True}]
        c = S.pick_candidates(self.sid, [self.tile(800000, "tile", 30000)], src)
        self.assertEqual([(x["author"], x["rule"]) for x in c],
                         [("tile", "page_order_live"), ("feed", "source_video")])
        self.assertEqual(S.pick_candidates(self.sid, [], src)[0]["author"], "feed")

    def test_source_video_can_be_the_origin(self):
        src = [{"id": _sid(T0 + 3, 3), "author": "maker", "plays": 10, "photo": False}]
        c = S.pick_candidates(self.sid, [self.tile(800000, "tile", 30000)], src)
        self.assertEqual((c[0]["author"], c[0]["rule"]), ("maker", "origin_video"))


class Rank(unittest.TestCase):
    def test_order(self):
        rows = [
            {"sound_id": "1", "is_original": False, "tier": 0, "video_count": 10 ** 6},
            {"sound_id": "2", "is_original": True, "tier": 2, "video_count": 50},
            {"sound_id": "3", "is_original": True, "tier": 1, "video_count": 5},     # low use
            {"sound_id": "4", "is_original": True, "tier": 1, "video_count": None},
            {"sound_id": "5", "is_original": True, "tier": 1, "video_count": 4000},
            {"sound_id": "6", "is_original": True, "tier": 0, "video_count": 20},
        ]
        got = [r["sound_id"] for r in sorted(rows, key=S.rank_key)]
        self.assertEqual(got, ["6", "5", "4", "2", "3", "1"])


class FakeEmbedNet(S.ReplayNet):
    pass


def _synthetic(n_sounds=3, embed=True, page=True, vc=1000):
    """n original sounds found on one hashtag page, each with an origin tile."""
    rec = {"tokchart": [], "apple": [{"title": "Song", "by": "Artist"}], "feed": [],
           "hashtag_id": {}, "hashtag_posts": {}, "sound_page": {}, "embed": {}}
    items = []
    for i in range(n_sounds):
        t = T0 + i * 100000
        sid = _sid(t, i)
        origin = _sid(t + 2, 50 + i)
        reuse = _sid(t + 500000, 60 + i)
        items.append({"video_id": reuse, "play_count": 1000 + i,
                      "author": {"unique_id": "reuser%d" % i},
                      "music_info": {"id": sid, "title": "original sound - maker%d" % i,
                                     "author": "Maker %d" % i}})
        if page:
            rec["sound_page"][sid] = {
                "embedInfo": {"id": sid, "videoCount": vc, "artist": "Maker %d" % i},
                "videoList": [{"id": reuse, "authorUniqueId": "reuser%d" % i, "playCount": 90000,
                               "privateItem": False, "playAddr": "x"},
                              {"id": origin, "authorUniqueId": "maker%d" % i, "playCount": 30000,
                               "privateItem": False, "playAddr": "x"}]}
        if embed:
            for v in (origin, reuse):
                rec["embed"][v] = {"music_id": sid, "sound_title": "original sound",
                                   "sound_author": "Maker %d" % i, "is_original": True,
                                   "playUrl": "x"}
    rec["hashtag_posts"]["14502:0"] = {"items": items, "cursor": 30, "has_more": False}
    return rec


def _run(rec, **kw):
    kw.setdefault("hashtags", "spedup:14502")
    kw.setdefault("feed_pages", 0)
    return S.build(S.ReplayNet(rec), sleep=_nosleep, **kw)


class BuildSynthetic(unittest.TestCase):
    def test_origin_chosen_and_verified(self):
        out, summ = _run(_synthetic())
        self.assertEqual(len(out), 3)
        for s in out:
            self.assertEqual(s["video"]["rule"], "origin_video")
            self.assertTrue(s["video"]["author"].startswith("maker"))
            self.assertIs(s["verified"], True)
            self.assertEqual(s["original_rule"], "title_original_sound")
            self.assertIn("tiktok_original_flag", s["original_signals"])
            self.assertEqual(s["sound_title"], "original sound - maker%s" % s["video"]["author"][-1])
        self.assertEqual(summ["sources"]["apple_rows_unlisted"], 1)
        self.assertEqual(summ["list"]["by_pick"], {"origin_video": 3})

    def test_embed_naming_another_sound_moves_to_the_next_video(self):
        rec = _synthetic(1)
        sid = list(rec["sound_page"])[0]
        origin = rec["sound_page"][sid]["videoList"][1]["id"]
        rec["embed"][origin]["music_id"] = "999"
        out, _ = _run(rec)
        self.assertEqual(out[0]["video"]["rule"], "page_order_live")
        self.assertIs(out[0]["verified"], True)

    def test_no_embed_keeps_the_page_tile_unverified(self):
        out, summ = _run(_synthetic(2, embed=False))
        self.assertEqual([s["verified"] for s in out], [False, False])
        self.assertEqual([s["video"]["rule"] for s in out], ["origin_video"] * 2)
        self.assertEqual(summ["list"]["unverified"], 2)

    def test_no_page_and_no_embed_drops_the_sound(self):
        out, summ = _run(_synthetic(2, embed=False, page=False))
        self.assertEqual(out, [])
        self.assertEqual(summ["dropped"], {"no video the embed confirms": 2})

    def test_no_page_but_embed_confirms_the_source_video(self):
        out, _ = _run(_synthetic(2, page=False))
        self.assertEqual([s["video"]["rule"] for s in out], ["source_video"] * 2)
        self.assertTrue(all(s["verified"] for s in out))

    def test_wall_stops_the_run(self):
        out, summ = _run(_synthetic(8, embed=False, page=False))
        self.assertTrue(summ["stopped_on_wall"])
        self.assertEqual(summ["sound_pages_tried"], S.STOP_AFTER_FAILS)

    def test_low_use_sounds_go_last_and_do_not_fill_the_quota(self):
        rec = _synthetic(3, vc=3)
        sid0 = sorted(rec["sound_page"])[0]
        rec["sound_page"][sid0]["embedInfo"]["videoCount"] = 5000
        out, summ = _run(rec, max_n=2)
        self.assertEqual(out[0]["sound_id"], sid0)
        self.assertEqual(summ["sound_pages_tried"], 3)   # two small ones did not count
        self.assertEqual(len(out), 2)

    def test_no_verify_makes_no_embed_call(self):
        net = S.ReplayNet(_synthetic(2))
        out, _ = S.build(net, hashtags="spedup:14502", feed_pages=0, verify=False, sleep=_nosleep)
        self.assertFalse([c for c in net.calls if c.startswith("embed:")])
        self.assertEqual([s["verified"] for s in out], [None, None])

    def test_cap_and_dedupe(self):
        rec = _synthetic(4)
        items = rec["hashtag_posts"]["14502:0"]["items"]
        items.append(copy.deepcopy(items[0]))          # the same sound twice
        out, summ = _run(rec, max_n=3)
        self.assertEqual(len(out), 3)
        self.assertEqual(len({s["sound_id"] for s in out}), 3)
        self.assertEqual(summ["pool"]["sounds"], 4)

    def test_a_failed_source_is_reported_not_fatal(self):
        rec = _synthetic(1)
        rec["tokchart"] = None
        out, summ = _run(rec)
        self.assertEqual(len(out), 1)
        self.assertTrue(any(e.startswith("tokchart") for e in summ["errors"]))


class Recorded(unittest.TestCase):
    """The real run, replayed."""

    @classmethod
    def setUpClass(cls):
        cls.rec = _load()
        cls.net = S.ReplayNet(copy.deepcopy(cls.rec))
        cls.out, cls.summ = S.build(cls.net, max_n=30, sleep=_nosleep)

    def test_replay_makes_no_unrecorded_page_or_embed_call(self):
        for c in self.net.calls:
            kind, _, key = c.partition(":")
            if kind == "sound_page":
                self.assertIn(key, self.rec["sound_page"])
            if kind == "embed":
                self.assertIn(key, self.rec["embed"])

    def test_list_is_capped_and_deduped(self):
        self.assertTrue(0 < len(self.out) <= 30)
        self.assertEqual(len({s["sound_id"] for s in self.out}), len(self.out))
        keys = [VC.vkey_of(S.video_url(s["video"]["author"], s["video"]["id"])) for s in self.out]
        self.assertTrue(all(keys))
        self.assertEqual(len(set(keys)), len(keys))

    def test_original_first(self):
        flags = [s["is_original"] for s in self.out]
        self.assertEqual(flags, sorted(flags, reverse=True))
        self.assertGreater(sum(flags), 0)

    def test_every_video_is_on_its_sound(self):
        for s in self.out:
            vid = s["video"]["id"]
            page = (self.rec["sound_page"].get(s["sound_id"]) or {}).get("videoList") or []
            on_page = any(str(v.get("id")) == vid for v in page)
            from_source = any(v["id"] == vid for v in s["source_videos"])
            self.assertTrue(on_page or from_source, s["sound_id"])
            if s.get("verified"):
                self.assertEqual(str(self.rec["embed"][vid]["music_id"]), s["sound_id"])

    def test_counts_add_up(self):
        L = self.summ["list"]
        self.assertEqual(L["original"] + L["official"], L["sounds"])
        self.assertEqual(sum(L["by_pick"].values()), L["sounds"])
        P = self.summ["pool"]
        self.assertEqual(P["original"] + P["official"], P["sounds"])

    def test_outputs_round_trip(self):
        d = tempfile.mkdtemp()
        out, side = os.path.join(d, "l.txt"), os.path.join(d, "l.jsonl")
        S.write_outputs(self.out, self.summ, out, side)
        urls = S.read_list(out)
        rows = [json.loads(x) for x in open(side)]
        self.assertEqual(len(urls), len(self.out))
        self.assertEqual([r["video_url"] for r in rows], urls)
        self.assertEqual([r["rank"] for r in rows], list(range(1, len(rows) + 1)))
        for r in rows:
            for k in ("sound_id", "sound_title", "author", "is_original", "original_rule",
                      "video_count", "video_url"):
                self.assertIn(k, r)
            self.assertEqual(r["video_key"], VC.vkey_of(r["video_url"]))
        # prewarm.py's own reading of a list file
        for ln in open(out):
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                self.assertTrue(ln.startswith("https://www.tiktok.com/@"))

    def test_replay_is_deterministic(self):
        again, _ = S.build(S.ReplayNet(copy.deepcopy(self.rec)), max_n=30, sleep=_nosleep)
        self.assertEqual([(s["sound_id"], s["video"]["id"]) for s in again],
                         [(s["sound_id"], s["video"]["id"]) for s in self.out])

    def test_fixture_is_scrubbed(self):
        blob = json.dumps(self.rec)
        for v in self.rec["sound_page"].values():
            for t in v.get("videoList") or []:
                self.assertRegex(t["authorUniqueId"], r"^user_\d+$")
                self.assertIn(t["playAddr"], ("x", ""))
        self.assertNotIn("tiktokcdn", blob)
        self.assertNotIn('"desc"', blob)


class RecorderScrub(unittest.TestCase):
    def test_names_become_placeholders(self):
        inner = S.ReplayNet(_synthetic(1))
        r = S.RecordingNet(inner)
        r.hashtag_posts("14502", 0)
        sid = list(inner.rec["sound_page"])[0]
        r.sound_page(sid)
        it = r.rec["hashtag_posts"]["14502:0"]["items"][0]
        self.assertRegex(it["author"]["unique_id"], r"^user_\d+$")
        self.assertRegex(it["music_info"]["title"], r"^original sound - user_\d+$")
        self.assertRegex(it["music_info"]["author"], r"^artist_\d+$")
        tiles = r.rec["sound_page"][sid]["videoList"]
        self.assertTrue(all(t["authorUniqueId"].startswith("user_") for t in tiles))


if __name__ == "__main__":
    unittest.main(verbosity=1)
