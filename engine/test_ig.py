#!/usr/bin/env python3
"""Offline tests for ig.py's logged-out routes (no network). Run: python3 test_ig.py

Replays the shapes measured on the droplet 2026-09-30 (server job B): an embed page that
carries video_url, one that says copyright_blocked and withholds it (the 0.26 s "error"),
a photo carousel, a deleted post, and a GraphQL answer that never comes back."""
import json, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ig


def embed_html(sm, blocked=False, lsd="LSDTOKEN1"):
    cj = {"context": {"type": sm.get("__typename", "GraphVideo"), "shortcode": "X",
                      "copyright_blocked": blocked},
          "gql_data": {"shortcode_media": sm}}
    inner = json.dumps(json.dumps(cj))[1:-1]      # as the page embeds it: an escaped string
    return ('<html><script>["LSD",[],{"token":"%s"}]</script>'
            '<script>{"contextJSON":"%s"}</script></html>' % (lsd, inner))


SM_VIDEO = {"__typename": "GraphVideo", "id": "1", "video_url": "https://cdn/v.mp4",
            "owner": {"username": "maker"}, "display_url": "https://cdn/t.jpg",
            "clips_music_attribution_info": {"artist_name": "Artist", "song_name": "Song",
                                             "uses_original_audio": False, "audio_id": "9"},
            "edge_media_to_caption": {"edges": [{"node": {"text": "cap"}}]}}
SM_BLOCKED = dict(SM_VIDEO)
SM_BLOCKED.pop("video_url")
SM_BLOCKED["clips_music_attribution_info"] = {"artist_name": "prime7zoir",
                                              "song_name": "Original audio",
                                              "uses_original_audio": True}


def gql(product=None, media=True):
    if not media:
        return {"data": {"xig_polaris_media": None}}
    m = {"__typename": "XIGPolarisMedia", "if_not_gated_logged_out": product}
    return {"data": {"xig_polaris_media": m}}


PRODUCT_REEL = {"pk": "42", "media_type": 2, "user": {"username": "prime7zoir"},
                "video_versions": [{"type": 101, "url": "https://scontent/v.mp4"}],
                "image_versions2": {"candidates": [{"url": "https://scontent/t.jpg"}]},
                "caption": {"text": "from graphql"}}
PRODUCT_PHOTOS = {"pk": "43", "media_type": 8, "user": {"username": "x"},
                  "carousel_media": [{"media_type": 1}, {"media_type": 1}]}


class Resp:
    def __init__(self, status=200, text="", js=None):
        self.status_code, self.text, self._js = status, text, js

    def json(self):
        if self._js is None:
            raise ValueError("not json")
        return self._js


class FakeSession:
    """Routes by URL. `script` maps a key to a Resp or an Exception to raise."""
    def __init__(self, script):
        self.script, self.calls = script, []

    def _hit(self, key):
        self.calls.append(key)
        r = self.script.get(key)
        if isinstance(r, list):
            r = r.pop(0) if r else None
        if isinstance(r, Exception):
            raise r
        return r or Resp(404, "", {"status": "fail"})

    def get(self, url, **kw):
        if "/embed/" in url:
            return self._hit("embed")
        if "get_ruling_for_content" in url:
            return self._hit("ruling")
        return self._hit("home")

    def post(self, url, **kw):
        assert kw["data"]["doc_id"] == ig.IG_GQL_DOC
        return self._hit("graphql")


class FakeCR:
    def __init__(self, sessions):
        self.sessions = list(sessions)

    def Session(self, impersonate=None):
        return self.sessions.pop(0)


class IGTests(unittest.TestCase):
    def setUp(self):
        self._cr, self._have = getattr(ig, "_cr", None), ig.HAVE_CFFI
        self._env = os.environ.pop("IG_LOCAL_SESSION", None)
        ig.HAVE_CFFI = True

    def tearDown(self):
        ig._cr, ig.HAVE_CFFI = self._cr, self._have
        if self._env is not None:
            os.environ["IG_LOCAL_SESSION"] = self._env

    def run_fetch(self, *sessions, code="Dd5RwwHomX9"):
        ig._cr = FakeCR(sessions)
        return ig.fetch_reel("https://www.instagram.com/reel/%s/" % code)

    def test_embed_with_video_is_one_request(self):
        s = FakeSession({"embed": Resp(200, embed_html(SM_VIDEO))})
        r = self.run_fetch(s)
        self.assertEqual(r["video_url"], "https://cdn/v.mp4")
        self.assertEqual(r["music"]["title"], "Song")
        self.assertNotIn("via", r)
        self.assertEqual(s.calls, ["embed"])

    def test_copyright_blocked_uses_graphql_and_keeps_embed_naming(self):
        s = FakeSession({"embed": Resp(200, embed_html(SM_BLOCKED, blocked=True)),
                         "graphql": Resp(200, "", gql(PRODUCT_REEL))})
        r = self.run_fetch(s)
        self.assertEqual(r["via"], "graphql")
        self.assertEqual(r["video_url"], "https://scontent/v.mp4")
        self.assertEqual(r["music"]["artist"], "prime7zoir")
        self.assertTrue(r["music"]["is_original"])
        self.assertEqual(r["owner"], "prime7zoir")
        self.assertEqual(r["caption"], "cap")
        self.assertEqual(s.calls, ["embed", "graphql"])

    def test_photo_carousel_says_photo_post(self):
        s = FakeSession({"embed": Resp(200, embed_html({"__typename": "GraphSidecar"})),
                         "graphql": Resp(200, "", gql(PRODUCT_PHOTOS))})
        with self.assertRaises(RuntimeError) as c:
            self.run_fetch(s)
        self.assertIn("photo post", str(c.exception))

    def test_carousel_with_a_video_slide_is_readable(self):
        p = dict(PRODUCT_PHOTOS, carousel_media=[
            {"media_type": 1}, {"media_type": 2, "video_versions": [{"url": "https://s/2.mp4"}]}])
        s = FakeSession({"embed": Resp(200, embed_html({"__typename": "GraphSidecar"})),
                         "graphql": Resp(200, "", gql(p))})
        self.assertEqual(self.run_fetch(s)["video_url"], "https://s/2.mp4")

    def test_deleted_says_video_is_unavailable(self):
        s = FakeSession({"embed": Resp(200, "<html>no context</html>"),
                         "graphql": Resp(200, "", gql(media=False)),
                         "ruling": Resp(404, "", {"message": "Media cannot be found",
                                                  "status": "fail"})})
        with self.assertRaises(RuntimeError) as c:
            self.run_fetch(s, code="DAAAAAAAAAA")
        self.assertIn("video is unavailable", str(c.exception))

    def test_restricted_ruling_says_age_restricted(self):
        s = FakeSession({"embed": Resp(200, embed_html(SM_BLOCKED, blocked=True)),
                         "graphql": Resp(200, "", gql(None)),
                         "ruling": Resp(200, "", {"title": "Restricted Video",
                                                  "description": "You must be 18 years old",
                                                  "status": "ok"})})
        with self.assertRaises(RuntimeError) as c:
            self.run_fetch(s)
        self.assertIn("age restricted", str(c.exception))

    def test_graphql_timeout_retries_once_then_says_did_not_answer(self):
        s1 = FakeSession({"embed": Resp(200, embed_html(SM_BLOCKED, blocked=True)),
                          "graphql": TimeoutError("20 s, 0 bytes")})
        s2 = FakeSession({"home": Resp(200, '<script id="__eqmc">{"l":"FRESH"}</script>'),
                          "graphql": Resp(200, "<!DOCTYPE html>", None)})
        with self.assertRaises(RuntimeError) as c:
            self.run_fetch(s1, s2)
        self.assertIn("did not answer", str(c.exception))
        self.assertEqual(s2.calls, ["home", "graphql"])

    def test_graphql_retry_can_succeed(self):
        s1 = FakeSession({"embed": Resp(200, embed_html(SM_BLOCKED, blocked=True)),
                          "graphql": Resp(200, "<!DOCTYPE html>", None)})
        s2 = FakeSession({"home": Resp(200, '<script id="__eqmc">{"l":"FRESH"}</script>'),
                          "graphql": Resp(200, "", gql(PRODUCT_REEL))})
        self.assertEqual(self.run_fetch(s1, s2)["via"], "graphql")

    def test_private_share_code_decodes_to_the_real_media_id(self):
        base = "Dd5RwwHomX9"
        self.assertEqual(ig.shortcode_to_mediaid(base + "A" * 28), ig.shortcode_to_mediaid(base))

    def test_ui_regexes_match_the_messages(self):
        # crate.html's error card keys off these substrings
        self.assertIn("photo post", ig._why_unreadable("X", "photo", None))
        self.assertIn("did not answer", ig._why_unreadable("X", "fail", None))


if __name__ == "__main__":
    unittest.main(verbosity=1)
