#!/usr/bin/env python3
# PROPRIETARY — CYAN ECLIPSE TECHNOLOGIES LLC · COMPANY INTELLECTUAL PROPERTY
# Eclipse Muster MCP — tenant store tests (production data path, 2026-11-16)
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
"""Tenant-local store path: init / import / verify / backup
round-trips, reject-without-provenance, persistence across a
server restart. Synthetic dataset only
(fixtures/tenant-synthetic/). Run: python3 -m unittest discover tests"""
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import tenant_store as T  # noqa: E402

DATASET = ROOT / "fixtures" / "tenant-synthetic" / "records.json"
SERVER = ROOT / "server.py"


class TestTenantStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / "tenant" / "state.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_init_creates_and_refuses_overwrite(self):
        out = T.do_init(self.state)
        self.assertTrue(out["created"])
        self.assertIn("ECLIPSE_MUSTER_STATE", out["state_path_contract"])
        doc = json.loads(self.state.read_text())
        self.assertEqual(doc["records"], {})
        self.assertEqual(doc["receipts"], [])
        with self.assertRaises(SystemExit):
            T.do_init(self.state)

    def test_import_verify_round_trip_counts(self):
        T.do_init(self.state)
        report = T.do_import(SERVER, self.state, DATASET)
        self.assertEqual(report["rejects"], [])
        self.assertEqual(report["ingested"], 50)
        self.assertEqual(report["counts"], {
            "person": 12, "company": 10, "deal": 8, "ticket": 8,
            "conversation": 6, "activity": 6})
        verify = T.do_verify(SERVER, self.state)
        self.assertTrue(verify["chain_valid"])
        self.assertEqual(verify["total_records"], 50)
        self.assertEqual(verify["counts"]["person"], 12)

    def test_import_rejects_record_without_provenance(self):
        T.do_init(self.state)
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps({"records": [{
            "kind": "person", "record_id": "person-no-prov",
            "fields": {"name": "No Prov"},
            "field_provenance": {},
            "data_rights": {"source_account": "tenant",
                            "legal_basis": "test"}}]}))
        report = T.do_import(SERVER, self.state, bad)
        self.assertEqual(report["ingested"], 0)
        self.assertEqual(len(report["rejects"]), 1)
        self.assertEqual(report["rejects"][0]["record_id"],
                         "person-no-prov")
        self.assertIn("PROVENANCE_MISSING",
                      report["rejects"][0]["error"])
        verify = T.do_verify(SERVER, self.state)
        self.assertEqual(verify["total_records"], 0)

    def test_import_requires_existing_store(self):
        with self.assertRaises(SystemExit):
            T.do_import(SERVER, self.state, DATASET)

    def test_backup_manifest_and_restore(self):
        T.do_init(self.state)
        T.do_import(SERVER, self.state, DATASET)
        backup = self.tmp / "backup" / "state-backup.json"
        manifest = T.do_backup(self.state, backup)
        self.assertEqual(manifest["sha256"],
                         hashlib.sha256(backup.read_bytes()).hexdigest())
        self.assertEqual(manifest["bytes"], backup.stat().st_size)
        self.assertTrue(Path(manifest["manifest_path"]).exists())
        self.assertIn("copy the backup file back", manifest["restore"])
        # Restore = byte copy back over the store path, nothing else.
        self.state.write_text("{}")  # simulate a damaged store file
        shutil.copyfile(backup, self.state)
        verify = T.do_verify(SERVER, self.state)
        self.assertTrue(verify["chain_valid"])
        self.assertEqual(verify["total_records"], 50)

    def test_persistence_across_server_restart(self):
        """Each Session is a fresh server process: import in one,
        verify + read in a second — counts and chain unchanged."""
        T.do_init(self.state)
        T.do_import(SERVER, self.state, DATASET)
        first = T.do_verify(SERVER, self.state)
        second = T.do_verify(SERVER, self.state)
        self.assertEqual(first["counts"], second["counts"])
        # Receipt count is NOT unchanged: reads are receipted, so
        # the first verify's six record_search calls append six
        # receipts the second verify then counts. Records + chain
        # are the persistence invariants; the ledger only grows.
        self.assertEqual(second["receipt_count"],
                         first["receipt_count"] + 6)
        self.assertTrue(second["chain_valid"])
        session = T.Session(SERVER, self.state)
        try:
            out, is_err = session.tool(
                "record_read", {"record_id": "person-tenant-0101",
                                "surface": "analytic"})
        finally:
            session.close()
        self.assertFalse(is_err)
        self.assertEqual(out["fields"]["emails"],
                         ["***@example.com"])
        self.assertIsNone(out["fields"]["gov_id"])


if __name__ == "__main__":
    unittest.main()
