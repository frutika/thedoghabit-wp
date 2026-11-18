"""apply_rework.load provjere. Pokretanje: python3 -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import apply_rework as ar  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.jpg").write_bytes(b"x")
        ar.ROOT = self.tmp

    def write(self, body, images):
        d = self.tmp / "rw"
        d.mkdir(exist_ok=True)
        (d / "post.html").write_text(body)
        (d / "meta.json").write_text(json.dumps({"post_id": 1, "slug": "s", "images": images}))
        return d

    def test_ok(self):
        d = self.write("<p>x</p>{{img:a}}<h2>FAQ</h2>", {"a": {"src": "a.jpg", "alt": "alt"}})
        _, _, keys = ar.load(d)
        self.assertEqual(keys, ["a"])

    def test_failures(self):
        cases = [
            ("{{img:a}}<h2>FAQ</h2>", {}),                                        # nema zapisa
            ("{{img:a}}<h2>FAQ</h2>", {"a": {"src": "nema.jpg", "alt": "x"}}),    # nema datoteke
            ("{{img:a}}<h2>FAQ</h2>", {"a": {"src": "a.jpg", "alt": ""}}),        # nema alt
            ("<h2>FAQ</h2>", {"a": {"src": "a.jpg", "alt": "x"}}),                # višak u meta
            ("{{img:a}}{{img:a}}<h2>FAQ</h2>", {"a": {"src": "a.jpg", "alt": "x"}}),  # duplikat
            ("{{img:a}}<h2>Faq</h2>", {"a": {"src": "a.jpg", "alt": "x"}}),       # nema markera
        ]
        for body, images in cases:
            with self.subTest(body=body, images=images), self.assertRaises(SystemExit):
                ar.load(self.write(body, images))

    def test_repo_rework_dirs_valid(self):
        ar.ROOT = Path("/opt/thedoghabit")  # slike žive samo na serveru (nisu u gitu)
        if not (ar.ROOT / "assets").is_dir():
            self.skipTest("nema /opt/thedoghabit/assets")
        for meta in (REPO / "content" / "rework").glob("*/meta.json"):
            with self.subTest(meta.parent.name):
                ar.load(meta.parent)


if __name__ == "__main__":
    unittest.main()
