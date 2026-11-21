#!/usr/bin/env python3
# PROPRIETARY — CYAN ECLIPSE TECHNOLOGIES LLC · COMPANY INTELLECTUAL PROPERTY
# Eclipse Muster MCP — mailbox ingest leg tests (Ring 1, synthetic)
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
"""Mail ingest: happy path, double-import idempotence, dedupe on
both keys, quarantine classes (server instruction quarantine +
leg whole-record Class-C quarantine), masking on read-back, chain
verify before/after, import-report contents. Fixtures are fully
synthetic (fixtures/mail-synthetic/). Every test here fails on the
pre-leg tree: tools/mail_ingest.py does not exist there.
Run: python3 -m unittest discover tests"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import mail_ingest as M  # noqa: E402
import tenant_store as T  # noqa: E402

FIXTURES = ROOT / "fixtures" / "mail-synthetic"
SERVER = ROOT / "server.py"
LABEL = "synthetic-owner-mailbox"
OWNER = "marta.quinn@cyaneclipse.example"


class TestMailIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / "state.json"
        T.do_init(self.state)
        self.export = self.tmp / "mail-export.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _import(self):
        return M.do_mail_import(SERVER, self.state, FIXTURES, LABEL,
                                OWNER, self.export)

    def _read(self, record_id, surface):
        session = T.Session(SERVER, self.state)
        try:
            out, is_err = session.tool(
                "record_read", {"record_id": record_id,
                                "surface": surface})
        finally:
            session.close()
        self.assertFalse(is_err)
        return out

    def _payloads(self):
        msgs = M.load_messages(FIXTURES)
        return M.build_payloads(msgs, LABEL, OWNER)

    def test_happy_path_counts(self):
        report = self._import()
        self.assertEqual(report["counts"],
                         {"added": 13, "duplicate": 0,
                          "quarantined": 1, "rejected": 0})
        self.assertEqual(report["messages_seen"], 14)
        verify = T.do_verify(SERVER, self.state)
        self.assertEqual(verify["counts"]["conversation"], 7)
        self.assertEqual(verify["counts"]["person"], 6)
        self.assertEqual(verify["total_records"], 13)

    def test_double_import_idempotent(self):
        first = self._import()
        self.assertEqual(first["counts"]["added"], 13)
        second = self._import()
        self.assertEqual(second["counts"]["added"], 0)
        self.assertEqual(second["counts"]["duplicate"], 13)
        verify = T.do_verify(SERVER, self.state)
        self.assertEqual(verify["total_records"], 13)

    def test_dedupe_both_keys(self):
        _, stats = self._payloads()
        # 07 = exact Message-ID duplicate of 01; 09 = content-hash
        # duplicate of the no-Message-ID 08.
        self.assertEqual(stats["duplicate_messages"], 2)
        payloads, _ = self._payloads()
        convs = [p for p in payloads if p["kind"] == "conversation"]
        thread_a = [c for c in convs
                    if c["fields"]["subject"]
                    == "Portal export failing"]
        self.assertEqual(len(thread_a), 1)
        self.assertEqual(len(thread_a[0]["fields"]["messages"]), 3)
        nomid = [c for c in convs if c["fields"]["subject"]
                 == "Workshop scheduling"]
        self.assertEqual(len(nomid), 1)
        self.assertEqual(len(nomid[0]["fields"]["messages"]), 1)
        self.assertTrue(nomid[0]["fields"]["messages"][0]
                        ["message_id"].startswith("hash:"))

    def test_class_c_whole_record_quarantine(self):
        payloads, stats = self._payloads()
        self.assertEqual(stats["quarantined_records"], 1)
        self.assertEqual(set(stats["class_c_classes"]),
                         {"gov_id_shape", "full_dob",
                          "residential_address"})
        blob = json.dumps(payloads)
        self.assertNotIn("123-45-6789", blob)
        self.assertNotIn("03/14/1988", blob)
        self.assertNotIn("Willow Lane", blob)
        self.assertNotIn("ana.ruiz", blob)
        self._import()
        verify = T.do_verify(SERVER, self.state)
        self.assertEqual(verify["total_records"], 13)

    def test_instruction_shaped_quarantined_by_server(self):
        self._import()
        payloads, _ = self._payloads()
        conv = [p for p in payloads if p["kind"] == "conversation"
                and p["fields"]["subject"] == "Refund demand"][0]
        out = self._read(conv["record_id"], "owner")
        msg = out["fields"]["messages"][0]
        self.assertTrue(msg["quarantined"])
        self.assertEqual(msg["trust"], "untrusted_data")

    def test_masking_on_readback(self):
        self._import()
        payloads, _ = self._payloads()
        person = [p for p in payloads if p["kind"] == "person"
                  and p["fields"]["emails"]
                  == ["priya.nair@example.org"]][0]
        analytic = self._read(person["record_id"], "analytic")
        self.assertEqual(analytic["fields"]["emails"],
                         ["***@example.org"])
        self.assertEqual(analytic["fields"]["name"], "P. N.")
        owner = self._read(person["record_id"], "owner")
        self.assertEqual(owner["fields"]["emails"],
                         ["priya.nair@example.org"])
        self.assertEqual(owner["fields"]["name"], "Priya Nair")
        # Provenance + Data Rights Ledger survive the round trip.
        self.assertEqual(
            analytic["provenance"]["emails"]["platform"],
            "mailbox-export")
        self.assertEqual(analytic["data_rights"]["source_account"],
                         LABEL)

    def test_chain_verify_before_after_and_report(self):
        report = self._import()
        self.assertTrue(report["chain_before"]["chain_valid"])
        self.assertTrue(report["chain_valid_after"])
        self.assertNotEqual(report["chain_before"]["chain_head"],
                            report["chain_after"]["chain_head"])
        self.assertGreater(report["chain_after"]["receipt_count"],
                           report["chain_before"]["receipt_count"])
        self.assertEqual(
            report["export_sha256"],
            hashlib.sha256(self.export.read_bytes()).hexdigest())
        on_disk = json.loads(Path(report["report_path"]).read_text())
        self.assertEqual(on_disk["counts"], report["counts"])
        self.assertEqual(on_disk["mailbox_label"], LABEL)

    def test_jsonl_source_parity(self):
        rows = []
        for msg in M.load_messages(FIXTURES):
            rows.append(json.dumps({
                "message_id": msg["message_id"],
                "folder": msg["folder"], "from": msg["from"],
                "to": msg["to"], "cc": msg["cc"],
                "subject": msg["subject"], "date": msg["date"],
                "body": msg["body"],
                "in_reply_to": msg["in_reply_to"],
                "references": msg["references"]}))
        jsonl = self.tmp / "export.jsonl"
        jsonl.write_text("\n".join(rows))
        report = M.do_mail_import(SERVER, self.state, jsonl, LABEL,
                                  OWNER, self.tmp / "j-export.json")
        self.assertEqual(report["counts"]["added"], 13)
        self.assertEqual(report["counts"]["quarantined"], 1)


if __name__ == "__main__":
    unittest.main()
