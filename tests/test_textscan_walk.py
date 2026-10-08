"""Walks of the hidden-text scan engine.

Adversary: instructions hidden in zero-width, bidirectional or tag characters; lookalike letters that
fake a trusted word; an emoji exemption turned into a smuggling channel; false alarms on ordinary
emoji and non-Latin titles; and findings that differ between machines.
"""

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from qew import textscan

TAG = lambda word: "".join(chr(0xE0000 + ord(c)) for c in word)  # noqa: E731
CANCEL = "\U000E007F"
SCOTLAND = "\U0001F3F4" + TAG("gbsct") + CANCEL
FAMILY = "\U0001F468‍\U0001F469‍\U0001F467"


class TextScanWalk(unittest.TestCase):
    def test_each_hidden_text_class_reports_exactly_its_own_finding(self) -> None:
        """A zero-width space, a right-to-left override, a tag payload and a Cyrillic letter among ASCII are told apart."""
        cases = {
            "pay​ment": ("zero_width",),
            "rm‮ -rf": ("bidi_control",),
            "title" + TAG("run it"): ("unicode_tags",),
            "pаyment": ("confusable",),
            "ａdmin": ("confusable",),
            "\U0001D41Aaa": ("confusable",),
            "Ⅰ item": ("confusable",),
            "ofﬁce": ("confusable",),
            "a⁠b؜": ("bidi_control", "zero_width"),
        }
        for text, expected in cases.items():
            with self.subTest(text=ascii(text)):
                self.assertEqual(textscan.scan(text), expected)

    def test_ordinary_emoji_and_plain_text_report_nothing(self) -> None:
        """Joiner families, the Scotland flag, a keycap and a skin-toned hand are not hidden text."""
        for text in (FAMILY, SCOTLAND, "1️⃣", "\U0001F44B\U0001F3FD", "❤️", "plain ASCII, punctuation; 100%"):
            with self.subTest(text=ascii(text)):
                self.assertEqual(textscan.scan(text), ())

    def test_the_emoji_exemption_is_not_a_smuggling_channel(self) -> None:
        """A black flag spelling an arbitrary word, a joiner next to an ASCII letter, a stray variation selector are reported."""
        self.assertEqual(textscan.scan("\U0001F3F4" + TAG("hello") + CANCEL), ("unicode_tags",))
        self.assertEqual(textscan.scan("\U0001F3F4" + TAG("gbsct")), ("unicode_tags",), "an unterminated tag sequence is not a flag")
        self.assertEqual(textscan.scan("\U0001F468‍a"), ("zero_width",))
        self.assertEqual(textscan.scan("a️"), ("zero_width",))

    def test_non_latin_titles_are_flagged_only_when_mixed_with_ascii(self) -> None:
        """A pure Cyrillic or Greek title is legitimate; the same word beside one ASCII letter is a lookalike risk."""
        self.assertEqual(textscan.scan("Привет мир"), ())
        self.assertEqual(textscan.scan("Ελλάδα"), ())
        self.assertEqual(textscan.scan("Привет x"), ("confusable",))
        self.assertEqual(textscan.scan("Ελλ 2024"), ("confusable",))

    def test_findings_do_not_depend_on_the_hosts_unicode_database(self) -> None:
        """With the host database unavailable the scan still gives the pinned answers (and the vocabulary digest holds)."""
        samples = ["pay​ment", "pаyment", FAMILY, SCOTLAND, "x" + TAG("hi")]
        script = (
            "import sys, json\n"
            "sys.modules['unicodedata'] = None\n"
            f"sys.path.insert(0, {os.path.join(ROOT, 'src')!r})\n"
            "from qew import textscan\n"
            f"print(json.dumps([textscan.scan(t) for t in {samples!r}]))\n"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [list(textscan.scan(t)) for t in samples])
        self.assertEqual(textscan.vocabulary_digest(), textscan.PINNED_DIGEST)


if __name__ == "__main__":
    unittest.main()
