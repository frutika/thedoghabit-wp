"""Testovi za --own-clip (vlastita snimka umjesto Pexels klipova).

Pokretanje: python3 -m unittest discover -s tests -v
Render test treba ffmpeg/ffprobe s libass; bez njih se preskače.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_video as gv  # noqa: E402

gv.log = lambda msg: None  # testovi ne pišu u logs/generate_video.log

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _script(words_per_segment, n=4):
    return {
        "yt_title": "Puppy meets the beach",
        "segments": [{"text": " ".join(["word"] * words_per_segment), "image_prompt": "dog"}
                     for _ in range(n)],
    }


class MaxWordsTest(unittest.TestCase):
    def test_scales_with_duration(self):
        self.assertEqual(gv.own_clip_max_words(19.0), 49)   # (19-0.5)*2.7
        self.assertEqual(gv.own_clip_max_words(10.0), 25)

    def test_capped_at_stock_limit(self):
        self.assertEqual(gv.own_clip_max_words(59.0), gv.SCRIPT_MAX_WORDS)

    def test_floor(self):
        self.assertEqual(gv.own_clip_max_words(2.0), 10)

    def test_violations_respect_custom_limit(self):
        script = _script(12)  # 48 riječi
        script["segments"][0]["text"] = " ".join(["word"] * 6)  # hook <= 10
        script["segments"][1]["text"] = " ".join(["word"] * 18)  # ukupno i dalje 48
        self.assertEqual(gv.script_violations(script), [])            # default 70
        self.assertTrue(gv.script_violations(script, max_words=40))   # snimka od ~15 s
        self.assertIn("48 riječi", gv.script_violations(script, max_words=40)[0])


class ArticleTextTest(unittest.TestCase):
    def test_note_goes_first_and_article_survives(self):
        text = gv.own_clip_article_text("Dogs sniff to read the world.",
                                        "Tery, JRT puppy, first time on a pebble beach", 19.4)
        self.assertTrue(text.startswith("VIDEO FOOTAGE (19 s"))
        self.assertIn("Tery, JRT puppy, first time on a pebble beach", text)
        self.assertTrue(text.endswith("Dogs sniff to read the world."))


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe nisu na PATH-u")
class RenderOwnClipTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        gv.build_ass([{"start": 0.2, "end": 0.8, "text": "Tery"},
                      {"start": 0.9, "end": 1.5, "text": "sniffs"}], self.dir / "subs.ass")

    def _make(self, name, args):
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(self.dir / name)],
                       check=True, stdin=subprocess.DEVNULL)

    def _clip(self, seconds, with_audio=True, size="1920x1080"):
        args = ["-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration={seconds}"]
        if with_audio:
            args += ["-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", "-shortest"]
        self._make("source.mp4", args + ["-pix_fmt", "yuv420p"])

    def _voice(self, seconds):
        self._make("voice.mp3", ["-f", "lavfi", "-i", f"sine=frequency=800:duration={seconds}"])
        return seconds

    def _probe(self, path):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height", "-of", "json", str(path)],
            capture_output=True, check=True).stdout
        import json
        return json.loads(out)

    def test_vertical_output_keeps_clip_length(self):
        self._clip(4)
        total = self._voice(2)
        out = gv.render_own_clip("source.mp4", total, self.dir, "t", {}, 0.15)
        info = self._probe(out)
        video = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (1080, 1920))
        self.assertTrue(any(s["codec_type"] == "audio" for s in info["streams"]))
        self.assertAlmostEqual(float(info["format"]["duration"]), 4.0, delta=0.2)

    def test_freezes_last_frame_when_narration_is_longer(self):
        self._clip(2, with_audio=False)
        total = self._voice(3)
        out = gv.render_own_clip("source.mp4", total, self.dir, "t", {}, 0.15)
        self.assertAlmostEqual(float(self._probe(out)["format"]["duration"]), 3.5, delta=0.2)


if __name__ == "__main__":
    unittest.main()
