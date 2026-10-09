"""Testovi za ugradnju Shorta u članak (generate_video.py).

Pokretanje: python3 -m unittest discover -s tests -v
"""
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_video as gv  # noqa: E402

gv.log = lambda *a, **k: None
ENV = {"WP_URL": "https://example.test", "WP_USER": "u", "WP_APP_PASSWORD": "p"}


class EmbedTests(unittest.TestCase):
    def test_private_video_is_skipped_and_wp_not_called(self):
        err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
        with mock.patch.object(gv, "http_request", side_effect=err) as req:
            entry = {"slug": "s", "post_id": 7, "youtube_id": "abcdefghijk", "status": "uploaded"}
            self.assertIn("nije javan", gv.embed_short(ENV, entry))
            self.assertEqual(req.call_count, 1)  # samo oEmbed, bez WP poziva
            self.assertNotIn("embedded", entry)

    def test_public_video_sets_meta(self):
        calls = []

        def fake(method, url, headers=None, data=None, timeout=30):
            calls.append((method, url, data))
            if "oembed" in url:
                return 200, b"{}"
            return 200, json.dumps({"meta": {gv.YT_META_KEY: "abcdefghijk"}}).encode()

        with mock.patch.object(gv, "http_request", side_effect=fake):
            entry = {"slug": "s", "post_id": 7, "youtube_id": "abcdefghijk", "status": "uploaded"}
            self.assertIsNone(gv.embed_short(ENV, entry))
        self.assertTrue(entry["embedded"])
        method, url, data = calls[-1]
        self.assertEqual((method, url), ("POST", "https://example.test/wp-json/wp/v2/posts/7"))
        self.assertEqual(json.loads(data), {"meta": {gv.YT_META_KEY: "abcdefghijk"}})

    def test_meta_not_saved_raises(self):
        with mock.patch.object(gv, "http_request", return_value=(200, b'{"meta": {}}')):
            with self.assertRaises(RuntimeError):
                gv.wp_set_youtube_meta(ENV, 7, "abcdefghijk")

    def test_backfill_skips_done_and_rendered_and_survives_errors(self):
        state = [
            {"slug": "done", "post_id": 1, "youtube_id": "aaaaaaaaaaa", "status": "uploaded", "embedded": True,
             "embed_v": gv.EMBED_VERSION},
            {"slug": "rendered", "post_id": 2, "youtube_id": None, "status": "rendered"},
            {"slug": "boom", "post_id": 3, "youtube_id": "bbbbbbbbbbb", "status": "uploaded"},
            {"slug": "ok", "post_id": 4, "youtube_id": "ccccccccccc", "status": "uploaded"},
        ]

        def fake_embed(env, entry):
            if entry["slug"] == "boom":
                raise RuntimeError("wp down")
            entry["embedded"] = True
            return None

        with mock.patch.object(gv, "embed_short", side_effect=fake_embed) as emb, \
                mock.patch.object(gv, "save_state") as save:
            done, skipped = gv.backfill_embeds(ENV, state)
        self.assertEqual(done, 1)
        self.assertEqual([s for s, _ in skipped], ["boom"])
        self.assertEqual(emb.call_count, 2)
        save.assert_called_once()


class VideoMetaTests(unittest.TestCase):
    def test_title_and_date_sent_with_id(self):
        sent = []

        def fake(method, url, headers=None, data=None, timeout=30):
            if "oembed" in url:
                return 200, b"{}"
            sent.append(json.loads(data))
            return 200, json.dumps({"meta": {gv.YT_META_KEY: "abcdefghijk"}}).encode()

        entry = {"slug": "s", "post_id": 7, "youtube_id": "abcdefghijk", "status": "uploaded",
                 "yt_title": "Why Dogs Yawn #shorts #dogs", "created": "2026-09-30 12:31:05"}
        with mock.patch.object(gv, "http_request", side_effect=fake):
            self.assertIsNone(gv.embed_short(ENV, entry))
        self.assertEqual(sent[-1]["meta"], {gv.YT_META_KEY: "abcdefghijk",
                                            gv.YT_TITLE_META_KEY: "Why Dogs Yawn",
                                            gv.YT_DATE_META_KEY: "2026-09-30"})
        self.assertEqual(entry["embed_v"], gv.EMBED_VERSION)

    def test_title_from_sidecar_when_missing_in_state(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "s").mkdir()
            (Path(d) / "s" / "s.json").write_text(json.dumps({"yt_title": "Crate Tips #shorts"}))
            with mock.patch.object(gv, "OUTPUT_DIR", Path(d)):
                self.assertEqual(gv.video_meta_for({"slug": "s", "created": "bad"}), ("Crate Tips", ""))

    def test_old_embeds_are_reprocessed_by_backfill(self):
        state = [{"slug": "old", "post_id": 1, "youtube_id": "aaaaaaaaaaa", "status": "uploaded", "embedded": True}]
        with mock.patch.object(gv, "embed_short", return_value=None) as emb, \
                mock.patch.object(gv, "save_state"):
            gv.backfill_embeds(ENV, state)
        emb.assert_called_once()


if __name__ == "__main__":
    unittest.main()
