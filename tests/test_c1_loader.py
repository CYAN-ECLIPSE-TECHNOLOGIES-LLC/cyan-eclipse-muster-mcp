# Eclipse Muster MCP — C1 loader tests (tests/test_c1_loader.py)
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
"""Unit tests for tools/c1_loader.py: index parsing, payload shape,
provenance completeness, and the fixture-store guard. The live
ingestion itself is proven on the estate store, not in this suite."""
import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location(
    "c1_loader", HERE.parent / "tools" / "c1_loader.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)

PEOPLE = """# People Index

- **Ada Example** — `~/memory/people/ada-example.md` — Engineer at Example Corp; contact ada@example.com, handle @adaex.
- **Bob Sample (Bobby)** — `~/memory/people/bob-sample.md` — A short summary with no email and no handle.
- A line that is not an entry.
"""

GROUPS = """# Groups Index

- **Example Team** — `~/memory/groups/example-team.md` — The example group; one line.
"""


class ParseTests(unittest.TestCase):
    def test_counts_and_kinds(self):
        people = loader.parse_index(PEOPLE, "person")
        groups = loader.parse_index(GROUPS, "company")
        self.assertEqual(len(people), 2)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["kind"], "company")
        self.assertEqual(groups[0]["record_id"], "company-example-team")

    def test_email_and_handle_extraction(self):
        ada = loader.parse_index(PEOPLE, "person")[0]
        self.assertEqual(ada["fields"]["emails"], ["ada@example.com"])
        self.assertEqual(ada["fields"]["handles"], ["@adaex"])
        bob = loader.parse_index(PEOPLE, "person")[1]
        self.assertNotIn("emails", bob["fields"])
        self.assertNotIn("handles", bob["fields"])
        self.assertEqual(bob["record_id"], "person-bob-sample")

    def test_provenance_and_data_rights_complete(self):
        for payload in loader.parse_index(PEOPLE, "person"):
            for field, prov in payload["field_provenance"].items():
                for key in ("platform", "platform_object_id", "source",
                            "observed_at", "adapter_version"):
                    self.assertTrue(prov.get(key), f"{field}:{key}")
                self.assertEqual(prov["source"], "import")
            self.assertTrue(payload["data_rights"]["source_account"])
            self.assertTrue(payload["data_rights"]["legal_basis"])

    def test_state_guard(self):
        with self.assertRaises(SystemExit):
            loader.guard_state_path(Path("/tmp/state.json"))
        loader.guard_state_path(Path("/tmp/state-c1-firm-graph.json"))


if __name__ == "__main__":
    unittest.main()
