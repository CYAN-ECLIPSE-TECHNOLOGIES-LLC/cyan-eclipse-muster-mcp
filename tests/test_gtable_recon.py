#!/usr/bin/env python3
# Eclipse Muster MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
"""Reconciliation sweep R1 (2026-11-20) — G-table deltas only.

Source of classes: the GAP-CLASS TABLE in
~/workspace/cyan-eclipse/priority-field-ops-20261119/
RYAN-BUGS-RECORD-20261120.md (Loadout zero-escape lane). The prior
ZTE pass covered classes C1–C7 in the owner's wording; this file
covers ONLY where the G-table's mechanism differs:

  G1/G2 — the table names Muster under "local services", but the
     mechanism needs a child the APP spawns. Muster's server code
     spawns none (grep evidence pinned below): the server IS a
     child of its MCP host, and its lifecycle budget is the host's
     concern. Verdict N/A, guarded by a test so the N/A stays
     true. (tools/c1_loader.py and tools/tenant_store.py DO spawn
     the server as a child — operator tooling, not the server;
     their lifecycle is the operator's act, out of the G1/G2
     mechanism as tabled.)
  G7 — the masking field-class map is data implemented from a
     written spec: README.md "Masking — before the boundary"
     (phone → partial last-2 every surface · email → owner full,
     analytic domain-only · names → initials on analytic ·
     government IDs / payment / health-adjacent → null in v1).
     Existing tests asserted fragments; this pins the full map.
  G3 — documentation contract against README.md / PYPI_README.md:
     the state-file location per platform + override, and a
     reset route.

Failing tests here are FINDINGS, left unweakened. Test code only.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import server as S  # noqa: E402


class TestG1G2NoChildSpawning(unittest.TestCase):
    """G1/G2 verdict for Muster: N/A — the server spawns no
    children, so there is no start/stop budget of its own and no
    helper it can orphan. Evidence (2026-11-20 grep):
    `grep -n "subprocess|Popen" server.py licence.py` → no match
    in either file. This guard keeps that evidence current."""

    def test_server_code_spawns_no_children(self):
        for name in ("server.py", "licence.py"):
            src = (ROOT / name).read_text()
            self.assertNotIn("subprocess", src, name)
            self.assertNotIn("Popen", src, name)
            self.assertNotIn("os.system", src, name)
            self.assertNotIn("os.popen", src, name)


class TestG7MaskingMapPin(unittest.TestCase):
    """Full-map pin against README.md's written masking table
    (the "Masking — before the boundary" paragraph), both
    directions, both surfaces, including the negative rows."""

    # README: "government IDs, payment data, and health-adjacent
    # fields → null in v1" — the code's marker tuple is the data
    # form of that row; pinned exactly, in order.
    EXPECTED_NULL_MARKERS = ("ssn", "gov_id", "tax_id", "passport",
                             "card_number", "payment", "iban",
                             "health", "diagnosis")
    NULL_FIELDS = ("ssn", "gov_id", "tax_id", "passport",
                   "card_number", "payment_info", "iban",
                   "health_status", "diagnosis")

    def test_g7_null_marker_table_exact(self):
        self.assertEqual(tuple(S.NULL_FIELD_MARKERS),
                         self.EXPECTED_NULL_MARKERS)

    def test_g7_null_class_masked_on_both_surfaces(self):
        for field in self.NULL_FIELDS:
            for surface in ("owner", "analytic"):
                self.assertIsNone(
                    S.mask_value(field, "secret-value", surface),
                    "%s on %s must be null (README masking table)"
                    % (field, surface))

    def test_g7_email_row(self):
        # README: email → full on owner's review surface,
        # domain-only on analytic/diagnostic surfaces.
        self.assertEqual(
            S.mask_value("email", "person@example.com", "owner"),
            "person@example.com")
        self.assertEqual(
            S.mask_value("email", "person@example.com", "analytic"),
            "***@example.com")

    def test_g7_phone_row_every_surface(self):
        # README: phone → partial (last 2) on EVERY surface; the
        # by-name class covers phone/mobile/tel field names.
        for field in ("phone", "mobile", "tel"):
            for surface in ("owner", "analytic"):
                out = S.mask_value(field, "+1 415 555 0132", surface)
                self.assertEqual(out, "***32",
                                 "%s on %s" % (field, surface))

    def test_g7_name_row(self):
        # README: names → initials on analytic surfaces; the owner
        # surface renders the name in full (negative row).
        self.assertEqual(
            S.mask_value("name", "Jordan Smith", "analytic"),
            "J. S.")
        self.assertEqual(
            S.mask_value("full_name", "Jordan Smith", "analytic"),
            "J. S.")
        self.assertEqual(
            S.mask_value("name", "Jordan Smith", "owner"),
            "Jordan Smith")

    def test_g7_unclassed_field_is_not_nulled(self):
        # Negative row of the map: a field in no class passes on
        # the owner surface (the table masks classes, not data).
        self.assertEqual(
            S.mask_value("city", "Tallahassee", "owner"),
            "Tallahassee")


class TestG3DocumentationContract(unittest.TestCase):
    """G3: user-facing text must name the app's persistent-state
    location(s) and a reset route. Muster's state contract is in
    server.py:81-105 (_default_state_path): ECLIPSE_MUSTER_STATE
    override, else the per-user data root. Temp leg: N/A — Muster
    is a stdio server installed as a wheel, not a onefile build;
    it writes no temp extraction (its only writes are the state
    file and policy.json beside it)."""

    @classmethod
    def setUpClass(cls):
        cls.readme = (ROOT / "README.md").read_text()
        cls.pypi = (ROOT / "PYPI_README.md").read_text()

    def test_g3_readme_names_state_locations(self):
        for needle in ("%LOCALAPPDATA%", "EclipseMuster",
                       "state.json", "Application Support",
                       "XDG_DATA_HOME", "ECLIPSE_MUSTER_STATE"):
            self.assertIn(needle, self.readme,
                          "README does not name %s" % needle)

    def test_g3_pypi_readme_names_state_location(self):
        for needle in ("EclipseMuster/state.json",
                       "ECLIPSE_MUSTER_STATE"):
            self.assertIn(needle, self.pypi,
                          "PYPI_README does not name %s" % needle)

    def test_g3_reset_route_named(self):
        # FINDING F-MUS-G3 (reset leg): both docs name the state
        # location, and README documents init (refuses to
        # overwrite — "a store is never silently reset"), import,
        # verify, backup and restore — but NO route to reset or
        # remove the store is ever named (e.g. "delete the state
        # file to start fresh"). A user who wants the footprint
        # gone is told where it lives but not that removing it is
        # the reset. Locations pass; the reset route is the gap.
        text = self.readme + "\n" + self.pypi
        route = re.search(
            r"delete the (state|store)|remove the (state|store)"
            r"|deleting the (state|store)|erase the store"
            r"|wipe the (state|store)|start fresh", text, re.I)
        self.assertIsNotNone(
            route,
            "neither README nor PYPI_README names a reset/remove "
            "route for the Muster state file")


if __name__ == "__main__":
    unittest.main()
