"""strip_unearned_claims (generate_post.py). Pokretanje: python3 -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_post as gp  # noqa: E402


class ClaimTests(unittest.TestCase):
    def test_rewrites_testing_claims(self):
        f = gp.strip_unearned_claims
        self.assertEqual(f("Best Calming Treats for Dogs: Anxiety Relief Options Tested"),
                         "Best Calming Treats for Dogs: Anxiety Relief Options Compared")
        self.assertEqual(f("We Tested 7 Harnesses"), "Compared 7 Harnesses")
        self.assertEqual(f("Hands-On Review of Dog Beds"), "Compared Review of Dog Beds")

    def test_leaves_normal_titles(self):
        for t in ["How to Stop Your Dog From Stealing Clothes", "Best Treat Pouch for Dog Training: Hands-Free Carriers",
                  "Testosterone and Dog Behavior"]:
            self.assertEqual(gp.strip_unearned_claims(t), t)


if __name__ == "__main__":
    unittest.main()
