#!/usr/bin/env python3
# Eclipse Muster MCP — licence gate tests (MUSTER-26 leg, 2026-11-16).
# PROPRIETARY — CYAN ECLIPSE TECHNOLOGIES LLC · COMPANY INTELLECTUAL PROPERTY

"""Licence gate tests. Hermetic: a fake LicenceClient, a fake clock,
licence state files in temp dirs. No network, no real licence.

Pins the house posture (License Server SPEC S12 model B, estate
pattern ETO-22): PAID_BOUNDARY_FEATURE = "graph_mutation" only;
activation-once; 4h heartbeat reuse; 72h offline grace
(LICENSE_GRACE); past window LICENSE_SUSPENDED_OFFLINE; a definitive
revocation never overridden by grace; no offline path for a
never-activated licence; reads / diagnostics never gated; unarmed
(no config) behaviour identical to v0.1.1.

Run: python3 -m unittest discover tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import licence as L  # noqa: E402
import server as S  # noqa: E402

T0 = 1_763_000_000.0
HOUR = 3600.0

FIXTURES = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "records.json")
    .read_text())["records"]


class FakeClient:
    def __init__(self):
        self.activate_calls = []
        self.checkin_calls = []
        self.activate_answer = {"state": "LICENSE_VALID",
                                "activation_token": "tok-fixture-1"}
        self.checkin_answer = {"state": "LICENSE_VALID"}

    def activate(self, artefact, install_id, install_label):
        self.activate_calls.append({"install_id": install_id})
        if isinstance(self.activate_answer, Exception):
            raise self.activate_answer
        return self.activate_answer

    def checkin(self, activation_token, artefact_hash):
        self.checkin_calls.append(
            {"activation_token": activation_token,
             "artefact_hash": artefact_hash})
        if isinstance(self.checkin_answer, Exception):
            raise self.checkin_answer
        return self.checkin_answer


def seed_state(dirpath, **over):
    path = Path(dirpath) / "licence-state.json"
    base = {"artefact": {"fixture": "signed-artefact"},
            "artefact_hash": "a" * 64}
    base.update(over)
    path.write_text(json.dumps(base))
    return path


def read_state(path):
    return json.loads(Path(path).read_text())


def make_gate(state_path, client, clock):
    return L.LicenceGate(state_path, client, now=lambda: clock["t"])


class TestGateBoundary(unittest.TestCase):
    def test_boundary_constant_is_the_named_graph_mutation(self):
        self.assertEqual(L.PAID_BOUNDARY_FEATURE, "graph_mutation")
        self.assertEqual(L.LICENCE_PRODUCT_ID, "muster")

    def test_non_boundary_feature_never_gated(self):
        with tempfile.TemporaryDirectory() as td:
            client = FakeClient()
            gate = make_gate(Path(td) / "absent.json", client,
                             {"t": T0})
            for feature in ("record_read", "record_search",
                            "policy_show", "receipt_list", "export"):
                d = gate.check(feature)
                self.assertTrue(d.allowed, feature)
            self.assertEqual(
                len(client.activate_calls) + len(client.checkin_calls),
                0)

    def test_armed_without_artefact_refuses_not_configured_typed(self):
        with tempfile.TemporaryDirectory() as td:
            client = FakeClient()
            gate = make_gate(Path(td) / "absent.json", client,
                             {"t": T0})
            d = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertFalse(d.allowed)
            self.assertEqual(d.state, "LICENSE_NOT_CONFIGURED")
            with self.assertRaises(L.LicenceRefusal) as ctx:
                gate.assert_allowed(L.PAID_BOUNDARY_FEATURE)
            self.assertEqual(ctx.exception.state,
                             "LICENSE_NOT_CONFIGURED")
            self.assertEqual(
                len(client.activate_calls) + len(client.checkin_calls),
                0)

    def test_malformed_state_file_is_not_configured_no_crash(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "licence-state.json"
            path.write_text("{not json")
            gate = make_gate(path, FakeClient(), {"t": T0})
            d = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertFalse(d.allowed)
            self.assertEqual(d.state, "LICENSE_NOT_CONFIGURED")


class TestGateActivationHeartbeat(unittest.TestCase):
    def test_activation_once_persists_token_and_install_id(self):
        with tempfile.TemporaryDirectory() as td:
            path = seed_state(td)
            client = FakeClient()
            clock = {"t": T0}
            gate = make_gate(path, client, clock)
            d1 = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertTrue(d1.allowed)
            self.assertEqual(d1.state, "LICENSE_VALID")
            self.assertEqual(len(client.activate_calls), 1)
            persisted = read_state(path)
            self.assertEqual(persisted["activation_token"],
                             "tok-fixture-1")
            self.assertIsInstance(persisted["install_id"], str)
            self.assertEqual(persisted["last_state"], "LICENSE_VALID")
            # state file is 0600 (key custody, P15)
            self.assertEqual(
                os.stat(path).st_mode & 0o777, 0o600)
            clock["t"] += 1 * HOUR
            d2 = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertTrue(d2.allowed)
            self.assertEqual(len(client.activate_calls), 1)
            self.assertEqual(len(client.checkin_calls), 0)

    def test_past_heartbeat_checkin_presents_token_and_hash_only(self):
        with tempfile.TemporaryDirectory() as td:
            path = seed_state(td)
            client = FakeClient()
            clock = {"t": T0}
            gate = make_gate(path, client, clock)
            gate.check(L.PAID_BOUNDARY_FEATURE)
            clock["t"] += 5 * HOUR
            d = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertTrue(d.allowed)
            self.assertEqual(len(client.checkin_calls), 1)
            self.assertEqual(
                client.checkin_calls[0],
                {"activation_token": "tok-fixture-1",
                 "artefact_hash": "a" * 64})


class TestGateRevokeGraceSuspend(unittest.TestCase):
    def _activated_gate(self, td):
        path = seed_state(td)
        client = FakeClient()
        clock = {"t": T0}
        gate = make_gate(path, client, clock)
        gate.check(L.PAID_BOUNDARY_FEATURE)
        return gate, client, clock

    def test_revoked_refuses_and_is_never_overridden_by_grace(self):
        with tempfile.TemporaryDirectory() as td:
            gate, client, clock = self._activated_gate(td)
            clock["t"] += 5 * HOUR
            client.checkin_answer = {"state": "LICENSE_REVOKED"}
            d = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertFalse(d.allowed)
            self.assertEqual(d.state, "LICENSE_REVOKED")
            clock["t"] += 1 * HOUR
            client.checkin_answer = L.LicenceTransportError("down")
            d2 = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertFalse(d2.allowed)
            self.assertEqual(d2.state, "LICENSE_REVOKED")

    def test_unreachable_inside_window_is_grace(self):
        with tempfile.TemporaryDirectory() as td:
            gate, client, clock = self._activated_gate(td)
            clock["t"] += 10 * HOUR
            client.checkin_answer = L.LicenceTransportError("down")
            d = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertTrue(d.allowed)
            self.assertEqual(d.state, "LICENSE_GRACE")

    def test_unreachable_past_window_is_suspended_offline(self):
        with tempfile.TemporaryDirectory() as td:
            gate, client, clock = self._activated_gate(td)
            clock["t"] += 80 * HOUR
            client.checkin_answer = L.LicenceTransportError("down")
            d = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertFalse(d.allowed)
            self.assertEqual(d.state, "LICENSE_SUSPENDED_OFFLINE")

    def test_never_activated_has_no_offline_path(self):
        with tempfile.TemporaryDirectory() as td:
            path = seed_state(td)
            client = FakeClient()
            client.activate_answer = L.LicenceTransportError("down")
            gate = make_gate(path, client, {"t": T0})
            d = gate.check(L.PAID_BOUNDARY_FEATURE)
            self.assertFalse(d.allowed)
            self.assertEqual(d.state, "LICENSE_NOT_CONFIGURED")


class TestArming(unittest.TestCase):
    def test_from_env_unarmed_without_state_path(self):
        self.assertIsNone(L.licence_gate_from_env({}))
        self.assertIsNone(
            L.licence_gate_from_env(
                {"ECLIPSE_MUSTER_LICENCE_STATE": "  "}))
        self.assertIsNone(L.licence_gate_from_env({}, {}))
        self.assertIsNone(
            L.licence_gate_from_env({}, {"licence": {}}))

    def test_from_env_armed_by_env_and_by_policy_block(self):
        gate = L.licence_gate_from_env(
            {"ECLIPSE_MUSTER_LICENCE_STATE": "/tmp/x.json"})
        self.assertIsInstance(gate, L.LicenceGate)
        gate2 = L.licence_gate_from_env(
            {}, {"licence": {"state_path": "/tmp/y.json",
                             "server_url": "http://127.0.0.1:9999"}})
        self.assertIsInstance(gate2, L.LicenceGate)
        self.assertEqual(gate2.client.base_url,
                         "http://127.0.0.1:9999")
        # env wins over the policy block
        gate3 = L.licence_gate_from_env(
            {"ECLIPSE_MUSTER_LICENCE_STATE": "/tmp/z.json"},
            {"licence": {"state_path": "/tmp/y.json"}})
        self.assertEqual(str(gate3.state_path), "/tmp/z.json")


class TestServerWiring(unittest.TestCase):
    """The gate as wired into server.py's handlers."""

    def setUp(self):
        self._saved_gate = S.LICENCE_GATE
        self.addCleanup(setattr, S, "LICENCE_GATE", self._saved_gate)

    def _arm(self, td, client, seed=True):
        path = seed_state(td) if seed else Path(td) / "absent.json"
        clock = {"t": T0}
        S.LICENCE_GATE = make_gate(path, client, clock)
        return clock

    def test_gate_off_by_default_and_tool_surface_unchanged(self):
        # No licence config in the test environment: no gate exists.
        self.assertIsNone(S._build_licence_gate())
        names = [t["name"] for t in S.tool_list()]
        self.assertEqual(names, [
            "record_ingest", "record_read", "record_search",
            "merge_propose", "merge_execute", "merge_unmerge",
            "reply_draft", "policy_show", "receipt_list",
            "retention_sweep"])
        state = S.empty_state()
        for rec in FIXTURES:
            S.t_record_ingest(state, rec)
        self.assertEqual(len(state["records"]), 5)
        self.assertFalse(any(r["type"] == "licence_refused"
                             for r in state["receipts"]))

    def test_gate_on_valid_write_class_serves(self):
        with tempfile.TemporaryDirectory() as td:
            self._arm(td, FakeClient())
            state = S.empty_state()
            out = S.t_record_ingest(state, FIXTURES[0])
            self.assertIn("record_id", out)

    def test_gate_on_not_configured_refuses_all_write_class(self):
        with tempfile.TemporaryDirectory() as td:
            self._arm(td, FakeClient(), seed=False)
            state = S.empty_state()
            gated_calls = [
                lambda: S.t_record_ingest(state, FIXTURES[0]),
                lambda: S.t_reply_draft(state, {"ticket_id": "t"}),
                lambda: S.t_merge_propose(
                    state, {"record_a": "a", "record_b": "b"}),
                lambda: S.t_merge_execute(
                    state, {"proposal_id": "p", "confirm_token": "x"}),
                lambda: S.t_merge_unmerge(
                    state, {"merge_id": "m", "confirm_token": "x"}),
                lambda: S.t_retention_sweep(
                    state, {"confirm": "PURGE_EXPIRED"}),
            ]
            for call in gated_calls:
                with self.assertRaises(S.ToolError) as ctx:
                    call()
                self.assertIn("LICENSE_NOT_CONFIGURED",
                              str(ctx.exception))
            refusals = [r for r in state["receipts"]
                        if r["type"] == "licence_refused"]
            self.assertEqual(len(refusals), 6)
            for r in refusals:
                self.assertEqual(set(r["detail"].keys()),
                                 {"tool", "licence_state", "tier",
                                  "verdict"})
                self.assertEqual(r["detail"]["verdict"], "DENY")
                # receipts carry no key material (spec 5.5 / P15)
                self.assertNotIn("tok-", json.dumps(r))
                self.assertNotIn("artefact", json.dumps(r["detail"]))
            self.assertEqual(len(state["records"]), 0)

    def test_gate_on_reads_and_diagnostics_never_refused(self):
        with tempfile.TemporaryDirectory() as td:
            state = S.empty_state()
            for rec in FIXTURES:
                S.t_record_ingest(state, rec)
            self._arm(td, FakeClient(), seed=False)
            read = S.t_record_read(
                state, {"record_id": "person-hubspot-1001",
                        "surface": "owner"})
            self.assertEqual(read["record_id"], "person-hubspot-1001")
            hits = S.t_record_search(state, {"kind": "person"})
            self.assertGreaterEqual(hits["count"], 1)
            self.assertIn("tier_floors", S.t_policy_show(state, {}))
            self.assertIn("receipts", S.t_receipt_list(state, {}))

    def test_gate_on_revoked_refuses_writes_reads_still_serve(self):
        with tempfile.TemporaryDirectory() as td:
            client = FakeClient()
            clock = self._arm(td, client)
            state = S.empty_state()
            S.t_record_ingest(state, FIXTURES[0])
            clock["t"] += 5 * HOUR
            client.checkin_answer = {"state": "LICENSE_REVOKED"}
            with self.assertRaises(S.ToolError) as ctx:
                S.t_record_ingest(state, FIXTURES[1])
            self.assertIn("LICENSE_REVOKED", str(ctx.exception))
            read = S.t_record_read(
                state, {"record_id": FIXTURES[0]["record_id"],
                        "surface": "analytic"})
            self.assertIn("fields", read)

    def test_gate_on_unreachable_never_activated_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            client = FakeClient()
            client.activate_answer = L.LicenceTransportError("down")
            self._arm(td, client)
            state = S.empty_state()
            with self.assertRaises(S.ToolError) as ctx:
                S.t_record_ingest(state, FIXTURES[0])
            self.assertIn("LICENSE_NOT_CONFIGURED",
                          str(ctx.exception))


class TestStdioPosture(unittest.TestCase):
    """End-to-end over stdio: unarmed == v0.1.1 surface; armed with
    no licence refuses writes by name and still serves reads."""

    def _run_server(self, extra_env, calls):
        here = Path(__file__).parent.parent
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ,
                       ECLIPSE_MUSTER_STATE=str(Path(td) / "s.json"))
            env.update(extra_env)
            proc = subprocess.Popen(
                [sys.executable, str(here / "server.py")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env)
            try:
                def rpc(rid, method, params=None):
                    proc.stdin.write(json.dumps(
                        {"jsonrpc": "2.0", "id": rid,
                         "method": method,
                         "params": params or {}}) + "\n")
                    proc.stdin.flush()
                    return json.loads(proc.stdout.readline())

                rpc(1, "initialize", {"protocolVersion": "2025-03-26"})
                out = [rpc(10 + i, "tools/call",
                           {"name": name, "arguments": args})
                       for i, (name, args) in enumerate(calls)]
            finally:
                proc.stdin.close()
                stderr = proc.stderr.read()
                proc.wait(timeout=10)
            return out, stderr

    def test_stdio_unarmed_surface_is_v011(self):
        out, stderr = self._run_server({}, [("record_search", {})])
        self.assertNotIn("licence gate ARMED", stderr)
        payload = json.loads(out[0]["result"]["content"][0]["text"])
        self.assertIn("hits", payload)

    def test_stdio_armed_no_licence_refuses_write_serves_read(self):
        with tempfile.TemporaryDirectory() as td:
            licence_state = str(Path(td) / "licence.json")
            out, stderr = self._run_server(
                {"ECLIPSE_MUSTER_LICENCE_STATE": licence_state},
                [("record_ingest", FIXTURES[0]),
                 ("record_search", {})])
            self.assertIn("licence gate ARMED", stderr)
            self.assertIn("LICENSE_NOT_CONFIGURED", stderr)
            refused = json.loads(
                out[0]["result"]["content"][0]["text"])
            self.assertIn("LICENSE_NOT_CONFIGURED",
                          refused["error"])
            self.assertTrue(out[0]["result"]["isError"])
            served = json.loads(out[1]["result"]["content"][0]["text"])
            self.assertIn("hits", served)


if __name__ == "__main__":
    unittest.main()
