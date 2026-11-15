"""Pauza objave (generate_post.py). Pokretanje: python3 -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_post as gp  # noqa: E402

gp.log = lambda *a, **k: None


class PauseTests(unittest.TestCase):
    def test_no_file_means_not_paused(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(gp, "PAUSE_PATH", Path(d) / "PUBLISHING_PAUSED"):
                self.assertFalse(gp.publishing_paused())

    def test_file_pauses_before_refill_and_alert(self):
        with tempfile.TemporaryDirectory() as d:
            pause = Path(d) / "PUBLISHING_PAUSED"
            pause.write_text("razlog\n", encoding="utf-8")
            with mock.patch.object(gp, "PAUSE_PATH", pause), \
                 mock.patch.object(sys, "argv", ["generate_post.py", "--publish"]), \
                 mock.patch.object(gp, "load_env") as env, \
                 mock.patch.object(gp, "refill_topics") as refill, \
                 mock.patch.object(gp, "send_alert") as alert:
                gp.main()  # bez SystemExit: izlaz 0
            env.assert_not_called()
            refill.assert_not_called()
            alert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
