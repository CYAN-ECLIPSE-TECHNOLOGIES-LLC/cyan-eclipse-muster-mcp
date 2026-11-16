"""CM-6 (Std 3.15) claim-inventory regression tests.

Coding Standard census 2026-11-14 + production-readiness run
2026-11-16: CM-6 FAIL on Muster — 5 licence/posture sentences on
the claim surfaces were untraced because no claim inventory of
record existed (estate ticket OPS-68, inventory leg). The cure is
CLAIM-INVENTORY.json at the tree root. These tests replicate the
checker's CM-6 extraction (checker/conformance/modules.py:
CLAIM_SENTENCE pattern, sentence flattening, sha256[:16] keys,
tree-licence tracing) with stdlib only, so the suite fails if a
future edit adds an untraced licence/posture/certification
sentence or lets the inventory's surface hashes go stale.
"""
import hashlib
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SURFACES = ["README.md", "PYPI_README.md", "bazaar-manifest.json"]
CLAIM_SENTENCE = re.compile(
    r"licen[cs]e|GPL|LGPL|AGPL|\bMIT\b|Apache|Proprietary|proprietary|"
    r"Internal use only|fork of|built on|based on|derived from|"
    r"certified|certification", re.I)


def _sentences(text):
    flat = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", flat)
            if s.strip()]


def _extracted():
    out = []
    for name in SURFACES:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            for sent in _sentences(f.read()):
                if CLAIM_SENTENCE.search(sent) and len(sent) > 12:
                    out.append((name, sent,
                                hashlib.sha256(sent.encode())
                                .hexdigest()[:16]))
    return out


def _tree_traced(sentence):
    # Mirrors the checker's tree-licence leg for this tree's MIT
    # LICENSE: a sentence naming the tree's own licence family as
    # a licence statement is traced by the tree itself.
    if re.search(r"fork of|built on|based on|derived from",
                 sentence, re.I):
        return False
    return "mit" in sentence.lower() and \
        re.search(r"licen[cs]e", sentence, re.I) is not None


class TestClaimInventoryCm6(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "CLAIM-INVENTORY.json"),
                  encoding="utf-8") as f:
            cls.inv = json.load(f)

    def test_inventory_file_present_and_parseable(self):
        self.assertEqual(self.inv["product"], "Cyan Eclipse Muster")
        self.assertTrue(self.inv["traces"])

    def test_every_extracted_sentence_is_traced(self):
        traced = {t["sentence_sha256"] for t in self.inv["traces"]}
        untraced = [(p, h) for p, s, h in _extracted()
                    if h not in traced and not _tree_traced(s)]
        self.assertEqual(untraced, [])

    def test_census_five_untraced_sentences_are_named(self):
        # The exact 5 instances enumerated 2026-11-16 (one per
        # surface occurrence; identical on main, the production
        # data-path branch and the licence-leg branch).
        traced = {t["sentence_sha256"] for t in self.inv["traces"]}
        for h in ("0f1dd2c45ce6afd3", "cccecf7a86d2685d",
                  "70e8368a3669be60", "d61205a930739a5b",
                  "eaa4d058bd5d450a"):
            self.assertIn(h, traced)

    def test_surface_hashes_current(self):
        for name in SURFACES:
            with open(os.path.join(ROOT, name), "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            self.assertEqual(self.inv["surface_hashes"][name],
                             digest, f"stale surface hash: {name}")

    def test_each_trace_carries_a_basis(self):
        for t in self.inv["traces"]:
            self.assertTrue(t.get("basis"), t["sentence_sha256"])
            self.assertTrue(t.get("sentence"), t["sentence_sha256"])


if __name__ == "__main__":
    unittest.main()
