# Eclipse Muster MCP — C1 slices 2-4 loader tests
# (tests/test_c1_loader_slices.py)
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
"""Unit tests for tools/c1_loader_slices.py: slice-2 intel dedupe
and DERIVED-band estimate marking, slice-3 never-use exclusions,
slice-4 register mapping and slice-1 collision diversion. The live
ingestion itself is proven on the estate store, not in this suite.
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location(
    "c1_loader_slices", HERE.parent / "tools" / "c1_loader_slices.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


def lead(key, sol, intel=True, band_n=2):
    base = {"key": key, "active": True, "agency": "AGENCY X",
            "deadline": "2027-01-01", "description_text": "",
            "est_value": None, "first_seen": "2026-11-01",
            "last_seen": "2026-11-13", "link": "https://example.test",
            "naics": "541511", "notice_id": key.split(":")[-1],
            "posted": "2026-10-01", "psc": "DA01",
            "sol_number": sol, "title": f"Title {sol}",
            "type": "Solicitation", "set_aside_desc": "",
            "flags": [], "reasons": [], "newcomer_reasons": [],
            "newcomer_score": 1, "rank_score": 1, "score": 1,
            "runway_bd": 1}
    if intel:
        base["intel"] = {
            "awards_examined": 3, "incumbents": [
                {"recipient": "INCUMBENT LLC"}],
            "implied_annual_band_usd_DERIVED": {"n": band_n,
                                                "median": 5.0}}
    return base


class Slice2Tests(unittest.TestCase):
    def test_intel_only_and_duplicate_posting_deduped(self):
        store = {"leads": {
            "sam:aaa": lead("sam:aaa", "SOL-1"),
            "sam:bbb": lead("sam:bbb", "SOL-1"),
            "sam:ccc": lead("sam:ccc", "SOL-2", intel=False),
        }, "runs": [], "version": 1}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "leads.json"
            p.write_text(json.dumps(store))
            out = loader.extract_slice2(p)
        deals = [r for r in out if r["kind"] == "deal"]
        self.assertEqual(len(deals), 1)
        self.assertEqual(deals[0]["external_ids"]["leadgen_keys"],
                         ["sam:aaa", "sam:bbb"])
        self.assertIn("DERIVED", deals[0]["fields"]
                      ["intel_band_basis"])
        # the disposition activities ride along (6 of record)
        acts = [r for r in out if r["kind"] == "activity"]
        self.assertEqual(len(acts), 6)


class Slice3Tests(unittest.TestCase):
    def test_never_use_addresses_absent(self):
        out = loader.extract_slice3()
        blob = json.dumps(out).lower()
        self.assertNotIn("partner@supabase.io", blob)
        self.assertNotIn("gmail", blob)
        persons = [r["fields"]["name"] for r in out
                   if r["kind"] == "person"]
        # prep-flagged names are not ingested as persons
        self.assertNotIn("Teo Gonzalez", persons)
        self.assertNotIn("Enes Akar", persons)
        self.assertNotIn("Luke Mulks", persons)
        self.assertEqual(len([r for r in out
                              if r["kind"] == "company"]), 12)
        self.assertEqual(len([r for r in out
                              if r["kind"] == "activity"]), 12)


class Slice4Tests(unittest.TestCase):
    REG = {"live": [{"id": "p1", "pursuit": "Pursuit One",
                     "agency": "Agency One", "deadline_et": "2027",
                     "status": "draft"}],
           "waiting": [{"id": "p1-wait", "pursuit": "Pursuit One",
                        "counterparty": "Agency One",
                        "submitted": "SENT 2026-01-01 - "
                                     "SENT-UNVERIFIED"}],
           "closed": [{"id": "old", "note": "SENT and closed."}]}

    def test_register_mapping_and_collision_diversion(self):
        with tempfile.TemporaryDirectory() as td:
            reg = Path(td) / "register.json"
            reg.write_text(json.dumps(self.REG))
            pkt = Path(td) / "va-vbecs-ss-20261113"
            pkt.mkdir()
            (pkt / "SEND-RECORD.md").write_text(
                "# Send record\n\n- To: Richard Melton, Contract "
                "Specialist — Richard.Melton2@va.gov\n- State: "
                "SENT-UNVERIFIED\n")
            out, collisions = loader.extract_slice4(
                reg, Path(td), [("person-richard-melton",
                                 "Richard Melton")])
            self.assertEqual(len(collisions), 1)
            self.assertEqual(collisions[0]["existing_record_id"],
                             "person-richard-melton")
            persons = [r["fields"]["name"] for r in out
                       if r["kind"] == "person"]
            self.assertNotIn("Richard Melton", persons)
            links = [r for r in out if r["record_id"].startswith(
                "activity-link-")]
            self.assertEqual(len(links), 1)
        deals = [r for r in out if r["kind"] == "deal"]
        self.assertEqual(len(deals), 3)
        note_only = [d for d in deals
                     if d["record_id"] == "deal-bid-old"][0]
        self.assertEqual(note_only["fields"]["name"], "old")
        sends = [r for r in out if r["kind"] == "activity"
                 and r["record_id"].startswith("activity-send-")]
        # waiting-row send + the fake packet's SEND-RECORD send
        self.assertEqual(len(sends), 2)


if __name__ == "__main__":
    unittest.main()
