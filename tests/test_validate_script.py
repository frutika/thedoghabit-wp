"""Jedinični testovi za validate_script / get_valid_script (generate_video.py).

Pokretanje: python3 -m unittest discover -s tests -v
"""
import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_video as gv  # noqa: E402

LOGS = []
gv.log = LOGS.append  # testovi ne pišu u logs/generate_video.log

# Haiku-style: ~128 riječi, markdown za TTS, puni opis pasmine u image_promptu.
HAIKU = {
    "yt_title": "Why Does Your Dog Chase Its Tail? The Surprising Truth Every Owner Should Know #shorts",
    "yt_description": "Tail chasing explained. #shorts #dogs",
    "yt_tags": ["dogs"],
    "pinned_comment": "Does your dog chase its tail?",
    "segments": [
        {"text": "Did you know that your dog chasing its tail could mean something much deeper than *just* play?",
         "image_prompt": "Small white and tan smooth-coat jack russell terrier spinning in circles chasing its tail in a sunny living room"},
        {"text": " ".join(["word"] * 30) + " because *they* are bored.",
         "image_prompt": "small white and tan smooth-coat jack russell terrier lying on a couch looking bored"},
        {"text": " ".join(["word"] * 35) + " 🐶 anxiety __matters__.",
         "image_prompt": "jack russell terrier with owner at the vet"},
        {"text": " ".join(["word"] * 30),
         "image_prompt": "golden retriever playing with puzzle toy on the floor"},
        {"text": "Want the full guide? Check the link in the description below for everything.",
         "image_prompt": "happy jack russell terrier running in park"},
    ],
}

# Opus-style (stvarni output s grane lumenta/short-script-opus, 63 riječi).
OPUS = {
    "yt_title": "When Is Tail Chasing a Red Flag?",
    "yt_description": "Tail chasing can be pure puppy fun, but frequent spinning usually means something else.",
    "yt_tags": ["dog tail chasing"],
    "pinned_comment": "Why does your dog chase their tail? A) pure fun B) boredom C) never does it",
    "segments": [
        {"text": "That spinning isn't always just fun.", "image_prompt": "jack russell chasing its tail"},
        {"text": "Your dog spins, snapping at their tail. It looks hilarious, so most owners just laugh.",
         "image_prompt": "dog spinning in circles indoors"},
        {"text": "Puppies do it for fun. But an adult spinning daily, unable to stop? That's different.",
         "image_prompt": "jack russell spinning on floor"},
        {"text": "Most likely it's boredom, so add walks and puzzle toys. Hair loss, scabs or scooting? See your vet.",
         "image_prompt": "jack russell playing puzzle toy"},
        {"text": "Does your dog spin? Tell me in the comments.", "image_prompt": "jack russell running in park"},
    ],
}

ERROR = {"error": "missing_input"}


class ValidateScriptTest(unittest.TestCase):
    def setUp(self):
        LOGS.clear()

    def test_haiku_style_ocisti_pa_padne_na_pragovima(self):
        script = copy.deepcopy(HAIKU)
        with self.assertRaises(ValueError) as cm:
            gv.validate_script(script)
        msg = str(cm.exception)
        self.assertIn("riječi naracije (max 70)", msg)
        self.assertIn("hook ima 17 riječi (max 10)", msg)
        self.assertIn("yt_title ima", msg)
        texts = " ".join(s["text"] for s in script["segments"])
        for bad in ("*", "_", "🐶"):
            self.assertNotIn(bad, texts)
        self.assertIn("because they are bored.", texts)
        prompts = [s["image_prompt"] for s in script["segments"]]
        for p in prompts:
            self.assertLessEqual(len(p.split()), gv.IMAGE_PROMPT_MAX_WORDS, p)
            for breed in ("jack russell", "terrier", "retriever", "smooth-coat"):
                self.assertNotIn(breed, p.lower())
        self.assertEqual(prompts[0], "dog spinning in circles chasing tail")
        self.assertTrue(any("maknuto:" in line for line in LOGS))

    def test_opus_style_prolazi(self):
        script = copy.deepcopy(OPUS)
        out = gv.validate_script(script)
        self.assertEqual(len(out["segments"]), 5)
        self.assertEqual([s["text"] for s in out["segments"]], [s["text"] for s in OPUS["segments"]])
        self.assertEqual(out["segments"][0]["image_prompt"], "dog chasing tail")
        self.assertEqual(out["segments"][1]["image_prompt"], "dog spinning in circles indoors")

    def test_error_json_dize_gresku(self):
        with self.assertRaises(gv.ScriptValidationError) as cm:
            gv.validate_script(copy.deepcopy(ERROR))
        self.assertIn("missing_input", str(cm.exception))

    def test_segmenti_izvan_raspona(self):
        script = copy.deepcopy(OPUS)
        script["segments"] = script["segments"][:3]
        with self.assertRaisesRegex(ValueError, "3 segmenata"):
            gv.validate_script(script)


class GetValidScriptTest(unittest.TestCase):
    def run_with(self, *outputs):
        calls = []

        def fake(post_title, article_text, breed, env, dry_run, retry_reason=None):
            calls.append(retry_reason)
            return copy.deepcopy(outputs[len(calls) - 1])

        with mock.patch.object(gv, "call_lumenta_script", side_effect=fake):
            try:
                return gv.get_valid_script("t", "a", "b", {}, False), calls
            except Exception as e:  # noqa: BLE001 — test hvata i vraća
                return e, calls

    def test_opus_prvi_put_bez_retryja(self):
        out, calls = self.run_with(OPUS)
        self.assertEqual(calls, [None])
        self.assertEqual(len(out["segments"]), 5)

    def test_haiku_pa_opus_jedan_retry_s_razlogom(self):
        out, calls = self.run_with(HAIKU, OPUS)
        self.assertEqual(len(calls), 2)
        self.assertIn("riječi naracije", calls[1])
        self.assertEqual(out["yt_title"], OPUS["yt_title"])

    def test_dvaput_haiku_nema_videa(self):
        err, calls = self.run_with(HAIKU, HAIKU)
        self.assertEqual(len(calls), 2)
        self.assertIsInstance(err, gv.ScriptValidationError)
        self.assertIn("ni u drugom pokušaju", str(err))

    def test_error_bez_retryja(self):
        err, calls = self.run_with(ERROR)
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(err, gv.ScriptValidationError)


class BuildDescriptionTest(unittest.TestCase):
    def test_hashtagovi_se_ne_ponavljaju(self):
        d = gv.build_description("Tail chasing explained. #Shorts #dogs #puppy #puppy", "https://x/p")
        self.assertEqual(d, "Tail chasing explained. #puppy\n\nRead the full guide: https://x/p\n\n#shorts #dogs #dogtraining")
        self.assertEqual(d.lower().count("#shorts"), 1)
        self.assertEqual(d.lower().count("#dogs"), 1)


if __name__ == "__main__":
    unittest.main()
