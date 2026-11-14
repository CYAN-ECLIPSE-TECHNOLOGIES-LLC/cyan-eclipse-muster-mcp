#!/usr/bin/env python3
# Eclipse Muster MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Eclipse Muster v0.1 (C0) tests.

"""Eclipse Muster v0.1 (C0) tests.
Run: python3 -m unittest discover tests"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import server as S  # noqa: E402

FIXTURES = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "records.json")
    .read_text())["records"]


def fresh_state():
    return S.empty_state()


def ingest_all(state):
    for rec in FIXTURES:
        S.t_record_ingest(state, rec)
    return state


class TestIngest(unittest.TestCase):
    def test_ingest_and_provenance_required(self):
        state = ingest_all(fresh_state())
        self.assertEqual(len(state["records"]), 5)
        bad = {"kind": "person", "record_id": "x",
               "fields": {"name": "No Prov"},
               "field_provenance": {},
               "data_rights": {"source_account": "a",
                               "legal_basis": "b"}}
        with self.assertRaises(S.ToolError) as ctx:
            S.t_record_ingest(state, bad)
        self.assertIn("PROVENANCE_MISSING", str(ctx.exception))

    def test_data_rights_required(self):
        state = fresh_state()
        bad = {"kind": "person", "fields": {"name": "X"},
               "field_provenance": {"name": {
                   "platform": "hubspot", "platform_object_id": "1",
                   "source": "import", "observed_at": "2026-10-24T00:00:00Z"}}}
        with self.assertRaises(S.ToolError) as ctx:
            S.t_record_ingest(state, bad)
        self.assertIn("DATA_RIGHTS_MISSING", str(ctx.exception))


class TestMasking(unittest.TestCase):
    def test_egress_masking(self):
        state = ingest_all(fresh_state())
        owner = S.t_record_read(
            state, {"record_id": "person-hubspot-1001",
                    "surface": "owner"})
        analytic = S.t_record_read(
            state, {"record_id": "person-hubspot-1001",
                    "surface": "analytic"})
        self.assertEqual(owner["fields"]["emails"],
                         ["maya.chen@acme.example"])
        self.assertEqual(analytic["fields"]["emails"],
                         ["***@acme.example"])
        # phone partial on BOTH surfaces; gov-id field nulled on both.
        self.assertTrue(str(owner["fields"]["phone"]).endswith("34"))
        self.assertNotIn("415", str(owner["fields"]["phone"]))
        self.assertIsNone(owner["fields"]["ssn_last4_note"])
        self.assertIsNone(analytic["fields"]["ssn_last4_note"])

    def test_receipts_carry_no_content(self):
        """Spec 5.5: receipts carry hashes/IDs, NEVER content — scan
        the whole ledger for ticket-body and email strings."""
        state = ingest_all(fresh_state())
        S.t_record_read(state, {"record_id": "ticket-zd-9001"})
        S.t_reply_draft(state, {"ticket_id": "ticket-zd-9001"})
        ledger = json.dumps(state["receipts"])
        self.assertNotIn("Invoice export fails", ledger)
        self.assertNotIn("maya.chen@acme.example", ledger)
        self.assertNotIn("refund my card", ledger.lower())
        self.assertNotIn("415-555-0134", ledger)


class TestMerge(unittest.TestCase):
    def test_propose_only_until_confirmed(self):
        state = ingest_all(fresh_state())
        out = S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        proposal = out["proposal"]
        # Proposing changed NOTHING in the graph.
        self.assertEqual(
            state["records"]["person-hubspot-1001"]["status"], "active")
        self.assertEqual(proposal["evidence"]["confidence"], 0.9)
        self.assertTrue(proposal["evidence"]["matched_keys"])
        # Wrong token = DENY + receipt, still nothing merged.
        with self.assertRaises(S.ToolError):
            S.t_merge_execute(state, {
                "proposal_id": proposal["proposal_id"],
                "confirm_token": "confirm-merge-wrong"})
        self.assertEqual(
            state["records"]["person-hubspot-1001"]["status"], "active")
        self.assertEqual(state["receipts"][-1]["type"],
                         "merge_execute_denied")
        # Correct token (bound to proposal hash) executes.
        token = S.expected_merge_token(proposal)
        res = S.t_merge_execute(state, {
            "proposal_id": proposal["proposal_id"],
            "confirm_token": token})
        merged = state["records"][res["merged_record_id"]]
        # Per-platform values retained; name conflict kept both ways.
        self.assertIn("name", merged["fields"])
        self.assertIn("name@zendesk", merged["fields"])
        self.assertEqual(
            state["records"]["person-hubspot-1001"]["status"],
            "merged_into")
        return state, res

    def test_unmerge_restores_prior_state(self):
        state, res = self.test_propose_only_until_confirmed()
        merge_id = res["merge_id"]
        before_a = json.dumps(
            state["merges"][merge_id]["snapshot"]["record_a"],
            sort_keys=True)
        out = S.t_merge_unmerge(state, {
            "merge_id": merge_id,
            "confirm_token": f"confirm-unmerge-{merge_id}"})
        self.assertEqual(sorted(out["restored_record_ids"]),
                         ["person-hubspot-1001", "person-zendesk-2002"])
        restored = state["records"]["person-hubspot-1001"]
        self.assertEqual(restored["status"], "active")
        self.assertEqual(
            json.dumps({k: v for k, v in restored.items()
                        if k != "status"}, sort_keys=True),
            json.dumps({k: v for k, v in
                        json.loads(before_a).items() if k != "status"},
                       sort_keys=True))
        self.assertNotIn(res["merged_record_id"], state["records"])


class TestDrafting(unittest.TestCase):
    def test_draft_only_from_record_facts(self):
        """The fixture ticket has NO resolution field: the draft must
        carry a MISSING_FACT marker and zero resolution claims."""
        state = ingest_all(fresh_state())
        out = S.t_reply_draft(state, {"ticket_id": "ticket-zd-9001"})
        draft = out["draft"]
        self.assertIn("MISSING_FACT", draft["body"])
        self.assertIn("resolution", draft["missing_facts"])
        self.assertIn("Invoice export fails with error 41",
                      draft["body"])
        self.assertTrue(draft["citations"])
        low = draft["body"].lower()
        self.assertNotIn("resolved", low.replace("resolution", ""))
        self.assertNotIn("fixed", low)
        self.assertNotIn("refund", low)

    def test_injection_quarantined_never_obeyed(self):
        """Herald Law #1: the instruction-shaped customer message is
        ingested as DATA — flagged quarantined, excluded from the
        draft's facts, and no action follows from it."""
        state = ingest_all(fresh_state())
        conv = state["records"]["conv-zd-9001"]
        msgs = {m["message_id"]: m
                for m in conv["fields"]["messages"]["value"]}
        self.assertTrue(msgs["m2"]["quarantined"])
        self.assertEqual(msgs["m2"]["trust"], "untrusted_data")
        self.assertFalse(msgs["m1"]["quarantined"])
        out = S.t_reply_draft(state, {"ticket_id": "ticket-zd-9001"})
        self.assertIn("m2", out["draft"]["quarantined_message_ids"])
        self.assertNotIn("billing@outside.example",
                         out["draft"]["body"])
        # No state anywhere records a refund/forward action.
        blob = json.dumps(state["records"]) + \
            json.dumps(state["drafts"])
        self.assertNotIn("refund_issued", blob)
        self.assertNotIn("forwarded", blob)


class TestPolicy(unittest.TestCase):
    def test_tier_floor_lowering_rejected_at_load(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "policy.json"
            bad.write_text(json.dumps(
                {"tier_overrides": {"customer_visible_send": 1}}))
            old = S.POLICY_PATH
            S.POLICY_PATH = bad
            try:
                with self.assertRaises(S.ToolError) as ctx:
                    S.load_policy()
                self.assertIn("TIER_FLOOR_VIOLATION", str(ctx.exception))
            finally:
                S.POLICY_PATH = old

    def test_tier_raise_allowed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            ok = Path(tmp) / "policy.json"
            ok.write_text(json.dumps(
                {"tier_overrides": {"customer_visible_send": 3}}))
            old = S.POLICY_PATH
            S.POLICY_PATH = ok
            try:
                policy = S.load_policy()
                self.assertEqual(
                    S.effective_tier(policy, "customer_visible_send"), 3)
            finally:
                S.POLICY_PATH = old


class TestChain(unittest.TestCase):
    def test_chain_valid_and_tamper_detected(self):
        state = ingest_all(fresh_state())
        S.t_record_search(state, {"kind": "person", "query": "maya"})
        out = S.t_receipt_list(state, {})
        self.assertTrue(out["chain_valid"])
        state["receipts"][1]["detail"]["verdict"] = "FORGED"
        self.assertFalse(S.verify_receipt_chain(state["receipts"]))

    def test_denylist_surface(self):
        names = {t["name"] for t in S.tool_list()}
        self.assertEqual(len(names), 9)
        for forbidden in S.FORBIDDEN_TOOL_NAMES:
            self.assertNotIn(forbidden, names)


class TestP10StdioLaunch(unittest.TestCase):
    """P10 (MCP face): the artefact as served launches and serves.

    An MCP stdio server has no installer; its launch proof is the
    subprocess launch of the shipped server.py: initialize handshake
    + the full tool list, from a clean process, temp state."""

    def test_stdio_launch_handshake_and_tools(self):
        import os
        import subprocess
        import tempfile
        here = Path(__file__).parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["ECLIPSE_MUSTER_STATE"] = str(Path(tmp) / "state.json")
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            proc = subprocess.Popen(
                [sys.executable, str(here / "server.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, cwd=str(here), env=env)
            try:
                def rpc(rid, method, params=None):
                    proc.stdin.write(json.dumps(
                        {"jsonrpc": "2.0", "id": rid, "method": method,
                         "params": params or {}}) + "\n")
                    proc.stdin.flush()
                    return json.loads(proc.stdout.readline())
                init = rpc(1, "initialize",
                           {"protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "p10-test",
                                           "version": "0"}})
                self.assertEqual(
                    init["result"]["serverInfo"]["name"], "eclipse-muster")
                tools = rpc(2, "tools/list")
                names = {t["name"]
                         for t in tools["result"]["tools"]}
                self.assertEqual(names, set(S.TOOLS.keys()))
                self.assertEqual(len(names), 9)
            finally:
                proc.stdin.close()
                proc.wait(timeout=10)
            self.assertEqual(proc.returncode, 0)


class TestP11PackagingCensus(unittest.TestCase):
    """P11 (MCP face): packaging completeness vs the declared set.

    Muster has NO native dependencies (pure Python stdlib) — the
    native-module census of P11 is NOT-APPLICABLE in kind; the
    applicable census is: every file the wheel/sdist force-include
    list declares actually exists, and server.py imports stdlib
    only, so no host-pruned dependency can be missing at runtime."""

    def test_force_included_files_exist(self):
        here = Path(__file__).parent.parent
        pyproject = (here / "pyproject.toml").read_text()
        declared = set()
        for line in pyproject.splitlines():
            line = line.strip()
            if line.startswith('"') and '" = "' in line:
                declared.add(line.split('"')[1])
        self.assertGreaterEqual(len(declared), 4)
        for rel in sorted(declared):
            self.assertTrue((here / rel).is_file(),
                            f"force-included file missing: {rel}")

    def test_stdlib_only_imports(self):
        import ast
        here = Path(__file__).parent.parent
        tree = ast.parse((here / "server.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0]
                                for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(imported)
        self.assertLessEqual(imported, set(sys.stdlib_module_names))


class TestP12StatePath(unittest.TestCase):
    """P12 (MCP face): runtime state goes only to the configured path.

    The server's one runtime-state file is explicit
    ($ECLIPSE_MUSTER_STATE, default ./data/state.json beside
    server.py — documented in the module docstring). Exercising the
    full tool surface over stdio with the env override set must
    write that file and nothing else anywhere."""

    def test_exercise_writes_only_configured_state(self):
        import os
        import subprocess
        import tempfile
        here = Path(__file__).parent.parent
        before = {p.relative_to(here)
                  for p in here.rglob("*") if p.is_file()
                  and ".git" not in p.parts
                  and "__pycache__" not in p.parts}
        fixtures = json.loads(
            (here / "fixtures" / "records.json").read_text())["records"]
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            env = dict(os.environ)
            env["ECLIPSE_MUSTER_STATE"] = str(state_path)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            proc = subprocess.Popen(
                [sys.executable, str(here / "server.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, cwd=tmp, env=env)
            try:
                def rpc(rid, method, params=None):
                    proc.stdin.write(json.dumps(
                        {"jsonrpc": "2.0", "id": rid, "method": method,
                         "params": params or {}}) + "\n")
                    proc.stdin.flush()
                    return json.loads(proc.stdout.readline())
                rpc(1, "initialize", {"protocolVersion": "2024-11-05"})
                rid = 10
                for rec in fixtures:
                    rid += 1
                    out = rpc(rid, "tools/call",
                              {"name": "record_ingest", "arguments": rec})
                    self.assertIn("result", out)
                rid += 1
                out = rpc(rid, "tools/call",
                          {"name": "receipt_list", "arguments": {}})
                self.assertIn("result", out)
            finally:
                proc.stdin.close()
                proc.wait(timeout=10)
            # The configured state file is the ONLY file in the cwd,
            # and it landed exactly where configured.
            self.assertTrue(state_path.is_file())
            self.assertEqual(
                [p.name for p in Path(tmp).iterdir()], ["state.json"])
            saved = json.loads(state_path.read_text())
            self.assertEqual(len(saved["records"]), len(fixtures))
        after = {p.relative_to(here)
                 for p in here.rglob("*") if p.is_file()
                 and ".git" not in p.parts
                 and "__pycache__" not in p.parts}
        self.assertEqual(before, after,
                         "exercise wrote files beside the server")

    def test_default_state_path_is_the_documented_one(self):
        self.assertEqual(S.STATE_PATH.name, "state.json")
        self.assertEqual(S.STATE_PATH.parent.name, "data")


class TestP13ClaimTrace(unittest.TestCase):
    """P13 (MCP face): public claims traced to the shipped surface.

    The README tool table must name exactly the tools the server
    actually serves; the status claims (C0, synthetic fixtures
    only, no credential, no platform contacted, bench pending)
    must be present in the public copy — an unscoped claim or a
    phantom tool is a defect."""

    def test_readme_tool_table_matches_registry(self):
        import re
        here = Path(__file__).parent.parent
        readme = (here / "README.md").read_text()
        claimed = set(re.findall(r"^\| `([a-z_]+)` \|", readme,
                                 flags=re.M))
        self.assertEqual(claimed, set(S.TOOLS.keys()))

    def test_status_claims_present_and_scoped(self):
        here = Path(__file__).parent.parent
        for doc in ("README.md", "PYPI_README.md"):
            text = (here / doc).read_text().lower()
            self.assertIn("synthetic", text, doc)
            self.assertIn("no credential", text, doc)
            self.assertIn("bench pending", text, doc)
            self.assertNotIn("certified", text, doc)

    def test_server_json_identity_matches_server(self):
        here = Path(__file__).parent.parent
        meta = json.loads((here / "server.json").read_text())
        self.assertEqual(meta["title"], "Cyan Eclipse Muster MCP")
        self.assertIn("Customer Graph", meta["description"])


class TestP14RefusalRecovery(unittest.TestCase):
    """P14 (MCP face): owner-confirm refusals name the remedy, are
    receipted, leave no stuck state, and recover in-session.

    The refusal surface of an MCP server is its tool error +
    receipt ledger: the error must state what was refused and the
    remedy (the owner-confirm token bound to the proposal hash),
    the denial must be receipted, and presenting the remedy in the
    SAME session (no restart, same state) must succeed."""

    def test_merge_refusal_then_remedy_same_session(self):
        state = ingest_all(fresh_state())
        out = S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        proposal = out["proposal"]
        with self.assertRaises(S.ToolError) as ctx:
            S.t_merge_execute(state, {
                "proposal_id": proposal["proposal_id"],
                "confirm_token": "confirm-merge-wrong"})
        msg = str(ctx.exception)
        # Remedy named in the refusal itself.
        self.assertIn("owner-confirm token", msg)
        self.assertIn("proposal hash", msg)
        # Denial receipted; nothing merged; proposal still usable.
        self.assertEqual(state["receipts"][-1]["type"],
                         "merge_execute_denied")
        self.assertEqual(state["receipts"][-1]["detail"]["verdict"],
                         "DENY")
        self.assertEqual(proposal["status"], "proposed")
        # Remedy in the same session succeeds — no restart.
        res = S.t_merge_execute(state, {
            "proposal_id": proposal["proposal_id"],
            "confirm_token": S.expected_merge_token(proposal)})
        self.assertIn("merged_record_id", res)
        # Unmerge refusal follows the same shape, then recovers.
        with self.assertRaises(S.ToolError) as ctx2:
            S.t_merge_unmerge(state, {
                "merge_id": res["merge_id"], "confirm_token": "nope"})
        self.assertIn("confirm", str(ctx2.exception).lower())
        self.assertEqual(state["receipts"][-1]["type"],
                         "merge_unmerge_denied")
        out2 = S.t_merge_unmerge(state, {
            "merge_id": res["merge_id"],
            "confirm_token": f"confirm-unmerge-{res['merge_id']}"})
        self.assertEqual(len(out2["restored_record_ids"]), 2)


if __name__ == "__main__":
    unittest.main()

# Eclipse Muster MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
