#!/usr/bin/env python3
# Eclipse Muster MCP — Zero-Test-Escapes pass (2026-11-20)
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai

"""ZTE pass: Ryan Owens' 7 Loadout v0.1.0 bug classes, translated
to this MCP server's surfaces. One class per TestCase below; the
class docstrings state applicability + what was already covered.

  C1 restart-after-interrupted-run   — TestZTEC1Restart
  C2 temp/runtime collision          — TestZTEC2Collision
  C3 undocumented leftover files     — TestZTEC3Leftovers
  C4 silent failure on dangling refs — TestZTEC4SilentFailure
  C5 demo/fixture reset incomplete   — TestZTEC5Reseed
  C6 duplicate block reasons         — TestZTEC6DuplicateReasons
  C7 checklist inconsistency         — TestZTEC7ChecklistAgreement

A failing test here is a FINDING, left as written (never weakened).
Run: python3 -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import licence as L  # noqa: E402
import server as S  # noqa: E402
import tenant_store as T  # noqa: E402

FIXTURES = json.loads((ROOT / "fixtures" / "records.json")
                      .read_text())["records"]
TENANT_DATASET = ROOT / "fixtures" / "tenant-synthetic" / "records.json"


def fresh_state():
    return S.empty_state()


def ingest_all(state):
    for rec in FIXTURES:
        S.t_record_ingest(state, rec)
    return state


def launch(state_path, cwd=None):
    env = dict(os.environ)
    env["ECLIPSE_MUSTER_STATE"] = str(state_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.Popen(
        [sys.executable, str(ROOT / "server.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, cwd=str(cwd or ROOT), env=env)


def rpc(proc, rid, method, params=None):
    proc.stdin.write(json.dumps(
        {"jsonrpc": "2.0", "id": rid, "method": method,
         "params": params or {}}) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise AssertionError("server closed the stream")
    return json.loads(line)


def tool_raw(proc, rid, name, args):
    return rpc(proc, rid, "tools/call",
               {"name": name, "arguments": args})


def tool_text(proc, rid, name, args):
    resp = tool_raw(proc, rid, name, args)
    return resp["result"]["content"][0]["text"]


def close(proc):
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
        proc.wait(timeout=10)


class TestZTEC1Restart(unittest.TestCase):
    """C1: a killed/interrupted server must restart cleanly.
    Already covered for the clean case only in part:
    test_tenant_store.test_persistence_across_server_restart
    (orderly restart, new process per Session). NOT covered
    before this file: SIGKILL between calls, and a state file
    truncated mid-save — save_state() writes the state file
    directly (write_text, no temp+replace), so a kill during a
    save leaves exactly the truncated file probed below."""

    def test_kill_between_calls_restarts_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            proc = launch(state)
            rpc(proc, 1, "initialize")
            out = json.loads(tool_text(proc, 2, "record_ingest",
                                       FIXTURES[0]))
            self.assertEqual(out["record_id"],
                             "person-hubspot-1001")
            proc.kill()  # interrupted run: SIGKILL, no shutdown
            proc.wait(timeout=10)
            proc = launch(state)
            try:
                init = rpc(proc, 1, "initialize")
                self.assertEqual(
                    init["result"]["serverInfo"]["name"],
                    "eclipse-muster")
                found = json.loads(tool_text(
                    proc, 2, "record_search", {"kind": "person"}))
                self.assertEqual(
                    [h["record_id"] for h in found["hits"]],
                    ["person-hubspot-1001"])
                ledger = json.loads(tool_text(
                    proc, 3, "receipt_list", {}))
                self.assertTrue(ledger["chain_valid"])
                # New work still lands after the restart.
                out = json.loads(tool_text(proc, 4, "record_ingest",
                                           FIXTURES[1]))
                self.assertEqual(out["record_id"],
                                 "person-zendesk-2002")
            finally:
                close(proc)

    def test_truncated_state_file_is_named_error_not_wedged(self):
        """Simulates the kill-during-save outcome: the state file
        holds a partial JSON document. The restarted server must
        either recover or refuse with a NAMED error about the
        state file — not answer every tools/call, forever, with
        the generic transport refusal that blames the request
        (MALFORMED_CONTAINER) and drops the request id."""
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            state.write_text(
                '{"records": {"person-hubspot-1001": {"record_id":'
                ' "person-hub')
            proc = launch(state)
            try:
                init = rpc(proc, 1, "initialize")
                self.assertEqual(
                    init["result"]["serverInfo"]["name"],
                    "eclipse-muster")
                for rid in (2, 3):
                    resp = tool_raw(proc, rid, "record_search",
                                    {"kind": "person"})
                    blob = json.dumps(resp)
                    self.assertNotIn("MALFORMED_CONTAINER", blob)
                    recovered = "result" in resp and \
                        resp["result"].get("isError") is not True
                    named_state_error = "state" in blob.lower()
                    self.assertTrue(
                        recovered or named_state_error,
                        f"call {rid}: neither recovery nor a named "
                        f"state error: {blob[:300]}")
            finally:
                close(proc)


class TestZTEC2Collision(unittest.TestCase):
    """C2: runtime-state collision across runs/instances. Muster
    has no archive extraction; its runtime temp state is the one
    state file (plus the licence gate's pid-named tmp file — see
    C3). README documents one server process per store file and
    no file locking, so the hostable deterministic legs are:
    distinct state paths must fully isolate two live instances,
    and sequential interleaving on one shared path must lose no
    record (the server reloads state per tools/call). A truly
    simultaneous write race stays a documented limitation, not
    a test — it cannot be driven deterministically."""

    def test_two_instances_distinct_state_paths_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            pa = launch(Path(td) / "a.json")
            pb = launch(Path(td) / "b.json")
            try:
                for proc in (pa, pb):
                    rpc(proc, 1, "initialize")
                json.loads(tool_text(pa, 2, "record_ingest",
                                     FIXTURES[0]))
                json.loads(tool_text(pb, 2, "record_ingest",
                                     FIXTURES[1]))
                hits_a = json.loads(tool_text(
                    pa, 3, "record_search", {"kind": "person"}))
                hits_b = json.loads(tool_text(
                    pb, 3, "record_search", {"kind": "person"}))
                self.assertEqual(
                    [h["record_id"] for h in hits_a["hits"]],
                    ["person-hubspot-1001"])
                self.assertEqual(
                    [h["record_id"] for h in hits_b["hits"]],
                    ["person-zendesk-2002"])
            finally:
                close(pa)
                close(pb)

    def test_two_instances_same_state_sequential_no_loss(self):
        with tempfile.TemporaryDirectory() as td:
            shared = Path(td) / "shared.json"
            pa = launch(shared)
            pb = launch(shared)
            try:
                for proc in (pa, pb):
                    rpc(proc, 1, "initialize")
                json.loads(tool_text(pa, 2, "record_ingest",
                                     FIXTURES[0]))
                json.loads(tool_text(pb, 2, "record_ingest",
                                     FIXTURES[1]))
                json.loads(tool_text(pa, 3, "record_ingest",
                                     FIXTURES[2]))
                on_disk = json.loads(shared.read_text())
                self.assertEqual(
                    sorted(on_disk["records"].keys()),
                    sorted([FIXTURES[0]["record_id"],
                            FIXTURES[1]["record_id"],
                            FIXTURES[2]["record_id"]]))
                hits = json.loads(tool_text(
                    pb, 3, "record_search", {}))
                self.assertEqual(hits["count"], 3)
            finally:
                close(pa)
                close(pb)


class TestZTEC3Leftovers(unittest.TestCase):
    """C3: a run must leave only the documented file set.
    Already covered for ingest+receipt_list only:
    TestP12StatePath.test_exercise_writes_only_configured_state,
    TestStatePath (default/override fresh-process writes), and
    the P15 stored-content census (content, not files). NOT
    covered before this file: the FULL tool surface (merge,
    draft, sweep, denial paths) over stdio, and the licence
    gate's tmp sidecar (licence.py persists via
    <name>.tmp-<pid> + os.replace — the tmp must never remain).
    Documented set: the one configured state file; the tenant
    backup pair (backup + .manifest.json, tenant_store only)."""

    def test_full_tool_surface_writes_only_configured_state(self):
        here = ROOT
        before = {p.relative_to(here) for p in here.rglob("*")
                  if p.is_file() and ".git" not in p.parts
                  and "__pycache__" not in p.parts}
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "work"
            work.mkdir()
            state = Path(td) / "store" / "state.json"
            proc = launch(state, cwd=work)
            try:
                rpc(proc, 1, "initialize")
                rid = 10
                for rec in FIXTURES:
                    rid += 1
                    json.loads(tool_text(proc, rid, "record_ingest",
                                         rec))
                rid += 1
                json.loads(tool_text(proc, rid, "record_read", {
                    "record_id": "person-hubspot-1001",
                    "surface": "analytic"}))
                rid += 1
                json.loads(tool_text(proc, rid, "record_search",
                                     {"kind": "person"}))
                rid += 1
                prop = json.loads(tool_text(
                    proc, rid, "merge_propose", {
                        "record_a": "person-hubspot-1001",
                        "record_b": "person-zendesk-2002"})
                    )["proposal"]
                rid += 1  # denial path also saves state
                denied = tool_text(proc, rid, "merge_execute", {
                    "proposal_id": prop["proposal_id"],
                    "confirm_token": "confirm-merge-wrong"})
                self.assertIn("CONFIRM_TOKEN_INVALID", denied)
                token = S.expected_merge_token(prop)
                rid += 1
                merged = json.loads(tool_text(
                    proc, rid, "merge_execute", {
                        "proposal_id": prop["proposal_id"],
                        "confirm_token": token}))
                rid += 1
                json.loads(tool_text(proc, rid, "merge_unmerge", {
                    "merge_id": merged["merge_id"],
                    "confirm_token":
                        f"confirm-unmerge-{merged['merge_id']}"}))
                rid += 1
                json.loads(tool_text(proc, rid, "reply_draft",
                                     {"ticket_id": "ticket-zd-9001"}))
                rid += 1
                json.loads(tool_text(proc, rid, "policy_show", {}))
                rid += 1
                json.loads(tool_text(proc, rid, "receipt_list", {}))
                rid += 1
                json.loads(tool_text(proc, rid, "retention_sweep",
                                     {"confirm": "PURGE_EXPIRED"}))
            finally:
                close(proc)
            self.assertEqual([p.name for p in work.iterdir()], [])
            self.assertEqual(
                [p.name for p in state.parent.iterdir()],
                ["state.json"])
        after = {p.relative_to(here) for p in here.rglob("*")
                 if p.is_file() and ".git" not in p.parts
                 and "__pycache__" not in p.parts}
        self.assertEqual(before, after,
                         "exercise wrote files beside the server")

    def test_licence_persist_leaves_no_tmp_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "licence-state.json"
            path.write_text(json.dumps({
                "artefact": {"fixture": "signed-artefact"},
                "artefact_hash": "a" * 64,
                "install_id": "install-fixture",
                "activation_token": "tok-fixture-1",
                "last_state": "LICENSE_VALID",
                "last_checkin_at": 0}))

            class FakeClient:
                def checkin(self, activation_token, artefact_hash):
                    return {"state": "LICENSE_VALID"}

                def activate(self, artefact, install_id, label):
                    return {"state": "LICENSE_VALID",
                            "activation_token": "tok-fixture-1"}

            gate = L.LicenceGate(path, FakeClient(),
                                 now=lambda: 1_763_000_000.0)
            decision = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertTrue(decision.allowed)  # checkin -> persist
            self.assertEqual([p.name for p in Path(td).iterdir()],
                             ["licence-state.json"])


class TestZTEC4SilentFailure(unittest.TestCase):
    """C4: a tool call naming a nonexistent record/proposal/
    merge/ticket id must surface a NAMED error — never silent
    success, never a silent empty result. Already covered in
    part: provenance/data-rights/kind validation on ingest
    (TestIngest), invalid surface on read+search
    (TestP1SentinelSuite), wrong confirm tokens (TestMerge,
    TestP14RefusalRecovery), tenant import rejects
    (test_tenant_store). NOT covered before this file: the
    dangling primary id of each tool."""

    def test_dangling_primary_ids_raise_named_errors(self):
        state = ingest_all(fresh_state())
        cases = [
            ("record_read unknown id",
             lambda: S.t_record_read(state, {"record_id": "nope-1"}),
             "unknown or merged record: nope-1"),
            ("merge_propose unknown record_a",
             lambda: S.t_merge_propose(
                 state, {"record_a": "nope-2",
                         "record_b": "person-hubspot-1001"}),
             "unknown or merged record: nope-2"),
            ("merge_propose unknown record_b",
             lambda: S.t_merge_propose(
                 state, {"record_a": "person-hubspot-1001",
                         "record_b": "nope-3"}),
             "unknown or merged record: nope-3"),
            ("merge_execute unknown proposal",
             lambda: S.t_merge_execute(
                 state, {"proposal_id": "prop-nope",
                         "confirm_token": "x"}),
             "unknown proposal: prop-nope"),
            ("merge_unmerge unknown merge",
             lambda: S.t_merge_unmerge(
                 state, {"merge_id": "merge-nope",
                         "confirm_token": "x"}),
             "unknown merge: merge-nope"),
            ("reply_draft unknown ticket",
             lambda: S.t_reply_draft(state, {"ticket_id": "nope-4"}),
             "unknown or merged record: nope-4"),
            ("reply_draft non-ticket record",
             lambda: S.t_reply_draft(
                 state, {"ticket_id": "person-hubspot-1001"}),
             "reply_draft requires a ticket record"),
        ]
        for label, call, expected in cases:
            with self.subTest(label=label):
                with self.assertRaises(S.ToolError) as ctx:
                    call()
                self.assertIn(expected, str(ctx.exception))
        # Stale reference: a merged-away source record is dangling.
        out = S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        prop = out["proposal"]
        S.t_merge_execute(state, {
            "proposal_id": prop["proposal_id"],
            "confirm_token": S.expected_merge_token(prop)})
        with self.assertRaises(S.ToolError) as ctx:
            S.t_record_read(state,
                            {"record_id": "person-hubspot-1001"})
        self.assertIn("unknown or merged record",
                      str(ctx.exception))

    def test_record_search_unknown_kind_is_named_error(self):
        """record_ingest validates kind against KINDS and refuses
        an unknown kind by name; record_search must do the same.
        A search naming a kind that can never exist returning
        {hits: [], count: 0} is indistinguishable from 'no such
        records' — the silent-empty archetype of this class."""
        state = ingest_all(fresh_state())
        with self.assertRaises(S.ToolError):
            S.t_record_search(state, {"kind": "bogus-kind",
                                      "query": ""})

    def test_reply_draft_unknown_person_id_is_named_error(self):
        """person_id is an explicit record reference: when the
        caller names one, a draft silently built from a DIFFERENT
        person (the ticket's requester link) — or from nobody —
        swallows the dangling reference. It must refuse by name,
        like every other primary-id argument."""
        state = ingest_all(fresh_state())
        with self.assertRaises(S.ToolError):
            S.t_reply_draft(state, {
                "ticket_id": "ticket-zd-9001",
                "person_id": "person-no-such-record"})


class TestZTEC5Reseed(unittest.TestCase):
    """C5: seeding/reset must leave no residual state. Muster
    has no in-server demo mode or reset tool: the seeding
    surfaces are fixture ingest (fixtures/records.json), the
    shipped synthetic tenant dataset via tenant_store import,
    and tenant_store init — which REFUSES to overwrite an
    existing store (test_tenant_store.
    test_init_creates_and_refuses_overwrite), so a store is
    never silently half-reset. NOT covered before this file:
    re-seeding the SAME corpus into a live store must not
    duplicate records, drafts, or proposals (receipts grow by
    design — the ledger is append-only, spec 5.5)."""

    def test_reseed_fixtures_no_duplicate_records_drafts_props(self):
        state = ingest_all(fresh_state())
        fields_before = {
            rid: json.dumps(rec["fields"], sort_keys=True)
            for rid, rec in state["records"].items()}
        S.t_reply_draft(state, {"ticket_id": "ticket-zd-9001"})
        S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        # Re-seed the whole demo corpus + re-run the demo steps.
        ingest_all(state)
        S.t_reply_draft(state, {"ticket_id": "ticket-zd-9001"})
        S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        self.assertEqual(len(state["records"]), len(FIXTURES))
        self.assertEqual(fields_before, {
            rid: json.dumps(rec["fields"], sort_keys=True)
            for rid, rec in state["records"].items()})
        self.assertEqual(len(state["drafts"]), 1)
        self.assertEqual(len(state["proposals"]), 1)

    def test_tenant_dataset_reimport_no_duplicate_records(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "tenant" / "state.json"
            T.do_init(state)
            first = T.do_import(T.DEFAULT_SERVER, state,
                                TENANT_DATASET)
            self.assertEqual(first["rejects"], [])
            verify1 = T.do_verify(T.DEFAULT_SERVER, state)
            second = T.do_import(T.DEFAULT_SERVER, state,
                                 TENANT_DATASET)
            self.assertEqual(second["rejects"], [])
            verify2 = T.do_verify(T.DEFAULT_SERVER, state)
            self.assertEqual(verify2["total_records"], 50)
            self.assertEqual(verify2["counts"], verify1["counts"])


class TestZTEC6DuplicateReasons(unittest.TestCase):
    """C6: one blocking reason must be rendered/listed once in
    one output. Surfaces: the merge/unmerge confirm refusals,
    the retention confirm refusal, merge-proposal evidence
    lists, draft missing-facts, and the K4 house refusal over
    stdio. Already covered in part: TestP14RefusalRecovery
    (refusal names the remedy, denial receipted — but not its
    uniqueness), TestMerge (denial receipt is the LAST receipt
    — but not the only one), and tests/test_k4_house_refusal.py
    (a standalone script with no unittest TestCase, so
    `unittest discover` never runs it)."""

    def test_refusal_reason_exactly_once_in_error_and_receipts(self):
        state = ingest_all(fresh_state())
        out = S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        prop = out["proposal"]
        with self.assertRaises(S.ToolError) as ctx:
            S.t_merge_execute(state, {
                "proposal_id": prop["proposal_id"],
                "confirm_token": "confirm-merge-wrong"})
        self.assertEqual(
            str(ctx.exception).count("CONFIRM_TOKEN_INVALID"), 1)
        self.assertEqual(
            sum(1 for r in state["receipts"]
                if r["type"] == "merge_execute_denied"), 1)
        res = S.t_merge_execute(state, {
            "proposal_id": prop["proposal_id"],
            "confirm_token": S.expected_merge_token(prop)})
        with self.assertRaises(S.ToolError) as ctx2:
            S.t_merge_unmerge(state, {
                "merge_id": res["merge_id"], "confirm_token": "x"})
        self.assertEqual(
            str(ctx2.exception).count("CONFIRM_TOKEN_INVALID"), 1)
        self.assertEqual(
            sum(1 for r in state["receipts"]
                if r["type"] == "merge_unmerge_denied"), 1)
        receipts_before = len(state["receipts"])
        with self.assertRaises(S.ToolError) as ctx3:
            S.t_retention_sweep(state, {})
        self.assertEqual(
            str(ctx3.exception).count("CONFIRM_REQUIRED"), 1)
        self.assertEqual(len(state["receipts"]), receipts_before)

    def test_evidence_and_draft_lists_have_no_duplicates(self):
        state = ingest_all(fresh_state())
        out = S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        evidence = out["proposal"]["evidence"]
        for key in ("matched_keys", "conflicts"):
            rendered = [json.dumps(e, sort_keys=True)
                        for e in evidence[key]]
            self.assertEqual(len(rendered), len(set(rendered)),
                             f"duplicate entries in {key}")
        draft = S.t_reply_draft(
            state, {"ticket_id": "ticket-zd-9001"})["draft"]
        self.assertEqual(len(draft["missing_facts"]),
                         len(set(draft["missing_facts"])))
        self.assertEqual(draft["body"].count("MISSING_FACT"), 1)
        citations = [json.dumps(c, sort_keys=True)
                     for c in draft["citations"]]
        self.assertEqual(len(citations), len(set(citations)))

    def test_k4_stdio_refusal_reason_once_and_server_alive(self):
        lines = [
            "this is not json",
            "[]",
            json.dumps({"jsonrpc": "2.0", "id": 99,
                        "method": "tools/call",
                        "params": "boom-not-a-dict"}),
            json.dumps({"jsonrpc": "2.0", "id": 100,
                        "method": "initialize", "params": {}}),
        ]
        with tempfile.TemporaryDirectory() as td:
            proc = launch(Path(td) / "state.json")
            try:
                out, _ = proc.communicate(
                    "\n".join(lines) + "\n", timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                self.fail("server hung on malformed input")
        for leak in ("Traceback", "AttributeError", "TypeError",
                     "KeyError", "ValueError"):
            self.assertNotIn(leak, out)
        answered = False
        for line in out.splitlines():
            self.assertLessEqual(
                line.count("MALFORMED_CONTAINER"), 1,
                f"refusal reason repeated in one output: {line}")
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("id") == 100 and "result" in obj:
                answered = True
        self.assertTrue(answered,
                        "server did not answer after the faults")


class TestZTEC7ChecklistAgreement(unittest.TestCase):
    """C7: two verification surfaces — or a surface vs the
    underlying state — must agree. Muster's checklist-like
    surfaces: tenant_store verify (chain + per-kind counts via
    the server) vs the store file itself; receipt_list's chain
    fields vs its integrity_warning object. Already covered in
    part: TestP13ClaimTrace (README table vs served registry),
    TestP5VersionLockstep (version surfaces), TestP6Integrity-
    Warning (warning names the seq — but never cross-checked
    against receipt_list's own divergence fields), and
    test_tenant_store.test_import_verify_round_trip_counts
    (verify vs import report for total + one kind only)."""

    def test_tenant_verify_agrees_with_direct_state_census(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "tenant" / "state.json"
            T.do_init(state)
            T.do_import(T.DEFAULT_SERVER, state, TENANT_DATASET)
            before = json.loads(state.read_text())
            self.assertTrue(
                S.verify_receipt_chain(before["receipts"]))
            verify = T.do_verify(T.DEFAULT_SERVER, state)
            after = json.loads(state.read_text())
            census = {}
            for rec in after["records"].values():
                if rec.get("status") == "active":
                    census[rec["kind"]] = \
                        census.get(rec["kind"], 0) + 1
            self.assertEqual(verify["counts"],
                             {k: census.get(k, 0)
                              for k in verify["counts"]})
            self.assertEqual(verify["total_records"],
                             sum(census.values()))
            self.assertEqual(verify["chain_valid"],
                             S.verify_receipt_chain(
                                 before["receipts"]))
            # receipt_list is read before verify's own searches
            # append theirs: its count is the pre-verify count.
            self.assertEqual(verify["receipt_count"],
                             len(before["receipts"]))

    def test_receipt_list_fields_agree_with_integrity_warning(self):
        state = ingest_all(fresh_state())
        S.t_record_search(state, {"kind": "person",
                                  "query": "maya"})
        clean = S.t_receipt_list(state, {})
        self.assertTrue(clean["chain_valid"])
        self.assertIsNone(clean["chain_divergence_seq"])
        self.assertIsNone(clean["chain_divergence_reason"])
        self.assertNotIn("integrity_warning", clean)
        state["receipts"][2]["detail"]["verdict"] = "FORGED"
        out = S.t_receipt_list(state, {})
        self.assertFalse(out["chain_valid"])
        warning = out["integrity_warning"]
        self.assertEqual(out["chain_divergence_seq"],
                         warning["divergence_seq"])
        self.assertEqual(out["chain_divergence_seq"],
                         state["receipts"][2]["seq"])
        self.assertIn(out["chain_divergence_reason"],
                      warning["message"])
        self.assertFalse(warning["chain_valid"])


if __name__ == "__main__":
    unittest.main()
