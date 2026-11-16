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


def _person_payload(record_id, fields):
    prov = {"platform": "cyan-eclipse-relationship-map",
            "platform_object_id": record_id,
            "source": "import",
            "observed_at": "2026-11-13",
            "adapter_version": "test"}
    return {"kind": "person", "record_id": record_id,
            "fields": fields,
            "field_provenance": {f: dict(prov) for f in fields},
            "data_rights": {"source_account": "test",
                            "legal_basis": "test",
                            "method": "test",
                            "pii_class": "strictest"}}


class TestContentMasking(unittest.TestCase):
    """GAP-C1-1: an email address inside a free-text field must mask
    on the analytic surface exactly as the email field class does,
    while the owner surface and ordinary prose stay byte-identical."""

    SUMMARY = ("President/CEO of Elite Business Strategies; the "
               "steven@cetech.ai gate probe cleared for the sends.")

    def _state_with_filed_case(self):
        state = fresh_state()
        S.t_record_ingest(state, _person_payload(
            "person-filed-case",
            {"name": "Ryan Owens",
             "emails": ["ryan@cetech.ai"],
             "summary": "CEO on the published team page; reachable "
                        "at ryan@cetech.ai."}))
        return state

    def test_filed_case_regression(self):
        state = self._state_with_filed_case()
        analytic = S.t_record_read(
            state, {"record_id": "person-filed-case",
                    "surface": "analytic"})
        # The prose email is masked with the email-class rendering;
        # the sentence-final period survives.
        self.assertEqual(
            analytic["fields"]["summary"],
            "CEO on the published team page; reachable "
            "at ***@cetech.ai.")
        self.assertEqual(analytic["fields"]["emails"],
                         ["***@cetech.ai"])
        self.assertNotIn("ryan@cetech.ai",
                         json.dumps(analytic["fields"]))
        # Owner surface renders the stored prose in full.
        owner = S.t_record_read(
            state, {"record_id": "person-filed-case",
                    "surface": "owner"})
        self.assertEqual(
            owner["fields"]["summary"],
            "CEO on the published team page; reachable "
            "at ryan@cetech.ai.")
        self.assertEqual(owner["fields"]["emails"],
                         ["ryan@cetech.ai"])
        # The stored record itself is untouched (egress-only cure).
        stored = state["records"]["person-filed-case"]
        self.assertEqual(stored["fields"]["summary"]["value"],
                         "CEO on the published team page; reachable "
                         "at ryan@cetech.ai.")

    def test_second_live_instance_and_search_surface(self):
        state = fresh_state()
        S.t_record_ingest(state, _person_payload(
            "person-second-case",
            {"name": "Princess Ousley", "summary": self.SUMMARY}))
        hits = S.t_record_search(
            state, {"kind": "person", "query": "Elite",
                    "surface": "analytic"})["hits"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(
            hits[0]["fields"]["summary"],
            "President/CEO of Elite Business Strategies; the "
            "***@cetech.ai gate probe cleared for the sends.")
        self.assertNotIn("steven@cetech.ai",
                         json.dumps(hits[0]["fields"]))

    def test_multiple_emails_one_field(self):
        state = fresh_state()
        S.t_record_ingest(state, _person_payload(
            "person-multi",
            {"name": "Multi Case",
             "summary": "Write to a@example.com, or "
                        "b@test.org; both read it."}))
        analytic = S.t_record_read(
            state, {"record_id": "person-multi",
                    "surface": "analytic"})
        self.assertEqual(
            analytic["fields"]["summary"],
            "Write to ***@example.com, or ***@test.org; "
            "both read it.")

    def test_phone_in_prose(self):
        """MUS-C1-005 names phone numbers in prose too; the phone
        field class renders last-2, so prose instances do the same
        (analytic only)."""
        state = fresh_state()
        S.t_record_ingest(state, _person_payload(
            "person-phone-prose",
            {"name": "Phone Case",
             "summary": "Call 415-555-0134 or (415) 555-0135; "
                        "the +1-415-555-0136 line is recorded."}))
        analytic = S.t_record_read(
            state, {"record_id": "person-phone-prose",
                    "surface": "analytic"})
        self.assertEqual(
            analytic["fields"]["summary"],
            "Call ***34 or ***35; the ***36 line is recorded.")
        self.assertNotIn("415", analytic["fields"]["summary"])
        owner = S.t_record_read(
            state, {"record_id": "person-phone-prose",
                    "surface": "owner"})
        self.assertIn("415-555-0134", owner["fields"]["summary"])

    def test_ordinary_prose_byte_identical(self):
        prose_cases = [
            "Led all eight desks at 1,475.93 (+47.59%) on "
            "2026-11-01; five straight days of outflows ~$1.44B.",
            "Version 0.1.0 shipped 2026-10-24; window "
            "2026-11-11 to 2027-02-11; calls at 08:22 ET.",
            "Known as GitHub @TayyDtb and @acevvswhite on the "
            "socials; no address printed.",
            "Reach him at ryan at cetech dot ai if the portal "
            "fails.",  # obfuscated shape: named false negative
            "Order 771-229 totals 2799 units; batch 36C10B27Q0056 "
            "is an identifier, not contact data.",
            "The unseparated string 4155550134 and the London "
            "number +44 20 7946 0958 are outside the NANP shape "
            "the scrub covers.",  # named false negatives
        ]
        for i, prose in enumerate(prose_cases):
            state = fresh_state()
            rid = f"person-prose-{i}"
            S.t_record_ingest(state, _person_payload(
                rid, {"name": "Prose Case", "summary": prose}))
            analytic = S.t_record_read(
                state, {"record_id": rid, "surface": "analytic"})
            self.assertEqual(analytic["fields"]["summary"], prose,
                             f"prose case {i} was mangled")

    def test_fixture_corpus_rendering_unchanged(self):
        """C0 scope guard: for every fixture record, fields outside
        the by-name classes render byte-identical to the stored
        values on BOTH surfaces (content scrub changes nothing)."""
        state = ingest_all(fresh_state())
        class_fields = {"emails", "email", "phone", "mobile", "tel",
                        "name", "full_name"}
        for rid, record in state["records"].items():
            if record.get("status") == "merged_into":
                continue
            for surface in ("analytic", "owner"):
                out = S.t_record_read(
                    state, {"record_id": rid, "surface": surface})
                for fname, fv in record["fields"].items():
                    lowered = fname.lower()
                    if lowered in class_fields or any(
                            m in lowered
                            for m in S.NULL_FIELD_MARKERS):
                        continue
                    # MUSTER-9 (P1): structured values (the
                    # conversation `messages` list-of-dicts) are now
                    # deep-masked on analytic — the fixture m2 body
                    # carries an email shape — so the byte-identical
                    # guard scopes to scalar fields; structured
                    # masking is asserted by TestP1SentinelSuite.
                    if isinstance(fv["value"], (dict, list)) and any(
                            isinstance(i, dict) for i in
                            (fv["value"] if isinstance(
                                fv["value"], list) else [])):
                        continue
                    self.assertEqual(
                        out["fields"][fname], fv["value"],
                        f"{rid}.{fname} changed on {surface}")


class TestP1SentinelSuite(unittest.TestCase):
    """QA-PATTERNS P1 / MUSTER-9: sentinel values in every field
    class, driven through every read/search/draft egress on every
    surface (including an invalid surface). Synthetic values only.
    Null-class shapes (SSN / payment card) must appear NOWHERE;
    email/phone shapes must not appear on analytic or in drafts;
    external_ids must never render raw."""

    SSN = "123-45-6789"
    CARD = "4111-1111-1111-1111"  # Luhn-valid test number
    EMAIL = "sentinel.person@secret.example"
    PHONE = "+1-415-555-0199"
    EXT = "EXTSENTINEL98765"

    def _state(self):
        state = fresh_state()
        S.t_record_ingest(state, _person_payload(
            "p-sentinel", {}) | {
                "kind": "person", "external_ids": {"hubspot": self.EXT},
                "fields": {
                    "name": "Sentinel Person",
                    "emails": [self.EMAIL], "phone": self.PHONE,
                    "notes": f"notes ssn {self.SSN} card {self.CARD} "
                             f"email {self.EMAIL} phone {self.PHONE}"},
                "field_provenance": {f: {
                    "platform": "probe", "platform_object_id": "p",
                    "source": "import", "observed_at": "2026-11-13"}
                    for f in ("name", "emails", "phone", "notes")}})
        conv = _person_payload("c-sentinel", {}) | {
            "kind": "conversation",
            "fields": {"messages": [
                {"author": "customer", "message_id": "x1",
                 "body": f"body email {self.EMAIL} ssn {self.SSN}"},
                {"author": "agent", "message_id": "x2",
                 "body": f"agent note card {self.CARD}"}]},
            "field_provenance": {"messages": {
                "platform": "probe", "platform_object_id": "c",
                "source": "import", "observed_at": "2026-11-13"}}}
        S.t_record_ingest(state, conv)
        ticket = _person_payload("t-sentinel", {}) | {
            "kind": "ticket", "external_ids": {"requester": "p-sentinel"},
            "fields": {
                "subject": f"Help card {self.CARD} email {self.EMAIL}",
                "status": "open",
                "resolution": f"paid {self.CARD} call {self.PHONE} "
                              f"ssn {self.SSN}"},
            "field_provenance": {f: {
                "platform": "probe", "platform_object_id": "t",
                "source": "import", "observed_at": "2026-11-13"}
                for f in ("subject", "status", "resolution")}}
        S.t_record_ingest(state, ticket)
        return state

    def test_no_sentinel_on_any_egress(self):
        state = self._state()
        outputs = {}
        for surface in ("analytic", "owner"):
            outputs[f"read-person-{surface}"] = S.t_record_read(
                state, {"record_id": "p-sentinel", "surface": surface})
            outputs[f"read-conv-{surface}"] = S.t_record_read(
                state, {"record_id": "c-sentinel", "surface": surface})
            outputs[f"search-{surface}"] = S.t_record_search(
                state, {"kind": "person", "query": "Sentinel",
                        "surface": surface})
        outputs["draft"] = S.t_reply_draft(
            state, {"ticket_id": "t-sentinel"})
        for label, out in outputs.items():
            blob = json.dumps(out)
            self.assertNotIn(self.SSN, blob, label)
            self.assertNotIn(self.CARD, blob, label)
            self.assertNotIn(self.EXT, blob, label)
            if label.endswith("analytic") or label == "draft" \
                    or label.startswith("search-analytic"):
                self.assertNotIn(self.EMAIL, blob, label)
                self.assertNotIn("415-555-0199", blob, label)
                self.assertNotIn("4155550199", blob, label)

    def test_invalid_surface_rejected_by_search_and_read(self):
        state = self._state()
        for tool, args in (
                (S.t_record_search, {"surface": "bogus"}),
                (S.t_record_read, {"record_id": "p-sentinel",
                                   "surface": "bogus"})):
            with self.assertRaises(S.ToolError):
                tool(state, args)

    def test_external_ids_render_as_hashes(self):
        state = self._state()
        out = S.t_record_read(state, {"record_id": "p-sentinel",
                                      "surface": "owner"})
        self.assertTrue(
            out["external_ids"]["hubspot"].startswith("sha256:"))

    def test_conversation_bodies_masked_on_analytic(self):
        state = self._state()
        out = S.t_record_read(state, {"record_id": "c-sentinel",
                                      "surface": "analytic"})
        body = " ".join(m["body"]
                        for m in out["fields"]["messages"])
        self.assertIn("***@secret.example", body)
        self.assertNotIn(self.SSN, body)
        self.assertNotIn(self.CARD, body)


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

    def test_preview_equals_apply_p8(self):
        """QA-PATTERNS P8 / MUSTER-15: for the fixture pair, the
        proposal's previewed merged record must equal the executed
        record field-for-field (compared through the analytic read
        surface, the preview's rendering), including metadata."""
        state = ingest_all(fresh_state())
        out = S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        proposal = out["proposal"]
        preview = proposal["preview_merged_record"]
        token = S.expected_merge_token(proposal)
        res = S.t_merge_execute(state, {
            "proposal_id": proposal["proposal_id"],
            "confirm_token": token})
        self.assertEqual(res["merged_record_id"],
                         preview["record_id"])
        applied = S.t_record_read(
            state, {"record_id": res["merged_record_id"],
                    "surface": "analytic"})
        self.assertEqual(applied["fields"], preview["fields"])
        self.assertEqual(applied["external_ids"],
                         preview["external_ids"])
        self.assertEqual(applied["data_rights"],
                         preview["data_rights"])
        self.assertEqual(applied["kind"], preview["kind"])
        stored = state["records"][res["merged_record_id"]]
        self.assertEqual(stored["merged_from"],
                         preview["merged_from"])

    def test_stale_preview_refused_p8(self):
        """If a source record changes after proposing, execute must
        refuse rather than apply a result the preview never showed."""
        state = ingest_all(fresh_state())
        out = S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        proposal = out["proposal"]
        state["records"]["person-zendesk-2002"]["fields"][
            "timezone"]["value"] = "America/New_York"
        with self.assertRaises(S.ToolError) as ctx:
            S.t_merge_execute(state, {
                "proposal_id": proposal["proposal_id"],
                "confirm_token": S.expected_merge_token(proposal)})
        self.assertIn("PREVIEW_DIVERGED", str(ctx.exception))

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

    def test_draft_carries_ai_attribution_p4(self):
        """QA-PATTERNS P4 / MUSTER-11: the draft object AND its body
        (the exported rendering) carry an explicit AI-agent author
        marker; no untouched/human content gains the marker (the
        marker exists only on drafts, which are wholly agent-made).
        """
        state = ingest_all(fresh_state())
        out = S.t_reply_draft(state, {"ticket_id": "ticket-zd-9001"})
        draft = out["draft"]
        self.assertEqual(draft["author"]["type"], "ai_agent")
        self.assertEqual(draft["author"]["tool"], "reply_draft")
        self.assertIn("AI-AGENT-AUTHORED", draft["attribution"])
        self.assertIn("AI agent", draft["body"])
        self.assertIn("reply_draft", draft["body"])
        # The stored draft (what an export reads) carries it too.
        stored = state["drafts"][draft["draft_id"]]
        self.assertEqual(stored["author"], draft["author"])
        self.assertIn("AI-AGENT-AUTHORED", stored["attribution"])

    def test_draft_greeting_blank_requester_name(self):
        """BUG-B regression: a whitespace-only requester name made
        str(name).split()[0] raise IndexError in the greeting. The
        draft must fall back to the neutral greeting; an empty name
        takes the same path."""
        for blank in ("   ", ""):
            state = ingest_all(fresh_state())
            person = state["records"]["person-zendesk-2002"]
            person["fields"]["name"]["value"] = blank
            out = S.t_reply_draft(state, {"ticket_id": "ticket-zd-9001"})
            first_line = out["draft"]["body"].splitlines()[0]
            self.assertEqual(first_line, "Hello,")

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


class TestP7Retention(unittest.TestCase):
    """QA-PATTERNS P7 / MUSTER-14: retention is enforced, expired
    values are destroyed byte-level, the sweep is confirm-gated,
    and merge snapshots do not survive unmerge."""

    SENTINEL = "RETENTION-SENTINEL-VALUE"

    def _old_record(self, state):
        S.t_record_ingest(state, _person_payload(
            "p-old", {"name": "Old Case", "notes": self.SENTINEL}))
        rec = state["records"]["p-old"]
        rec["ingested_at"] = "2026-01-01T00:00:00+00:00"
        rec["data_rights"]["retention"] = "graph-cache-30d"
        return rec

    def test_sweep_confirm_gated_and_byte_level(self):
        state = fresh_state()
        self._old_record(state)
        with self.assertRaises(S.ToolError) as ctx:
            S.t_retention_sweep(state, {})
        self.assertIn("CONFIRM_REQUIRED", str(ctx.exception))
        # The gate fired before any destruction.
        self.assertIn(self.SENTINEL, json.dumps(state["records"]))
        out = S.t_retention_sweep(state, {"confirm": "PURGE_EXPIRED"})
        self.assertEqual(out["dispositioned_record_ids"], ["p-old"])
        self.assertNotIn(self.SENTINEL, json.dumps(state["records"]))
        rec = state["records"]["p-old"]
        self.assertEqual(rec["status"], "retention_expired")
        self.assertEqual(rec["fields"], {})
        self.assertTrue(
            rec["retention_disposition"]["fields_hash"])

    def test_retention_enforced_on_read(self):
        state = fresh_state()
        self._old_record(state)
        out = S.t_record_read(state, {"record_id": "p-old"})
        self.assertEqual(out["fields"], {})
        self.assertNotIn(self.SENTINEL, json.dumps(out))

    def test_unexpired_record_untouched(self):
        state = fresh_state()
        S.t_record_ingest(state, _person_payload(
            "p-new", {"name": "New Case", "notes": self.SENTINEL}))
        out = S.t_retention_sweep(state, {"confirm": "PURGE_EXPIRED"})
        self.assertEqual(out["count"], 0)
        self.assertIn(self.SENTINEL, json.dumps(state["records"]))

    def test_snapshot_purged_on_unmerge(self):
        state = ingest_all(fresh_state())
        out = S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        proposal = out["proposal"]
        res = S.t_merge_execute(state, {
            "proposal_id": proposal["proposal_id"],
            "confirm_token": S.expected_merge_token(proposal)})
        merge = state["merges"][res["merge_id"]]
        # While active, the snapshot exists (it IS the unmerge path).
        self.assertIn("record_a", merge["snapshot"])
        S.t_merge_unmerge(state, {
            "merge_id": res["merge_id"],
            "confirm_token": f"confirm-unmerge-{res['merge_id']}"})
        merge = state["merges"][res["merge_id"]]
        self.assertEqual(merge["snapshot"]["disposition"],
                         "purged_on_unmerge")
        self.assertNotIn("record_a", merge["snapshot"])
        self.assertTrue(merge["snapshot"]["snapshot_hash"])


class TestP5VersionLockstep(unittest.TestCase):
    """QA-PATTERNS P5 / MUSTER-12: every version surface in the repo
    must agree — server.json (both fields), pyproject, VERSION,
    server.py docstring + serverInfo, README status, manifest
    status. A binary change without a bump breaks this test the
    next time any surface is edited alone."""

    ROOT = Path(__file__).parent.parent

    def test_all_version_surfaces_agree(self):
        version = (self.ROOT / "VERSION").read_text().strip()
        self.assertEqual(version, "0.1.1")
        pyproject = (self.ROOT / "pyproject.toml").read_text()
        self.assertIn(f'version = "{version}"', pyproject)
        server_json = json.loads((self.ROOT / "server.json")
                                 .read_text())
        self.assertEqual(server_json["version"], version)
        self.assertEqual(server_json["packages"][0]["version"],
                         version)
        server_py = (self.ROOT / "server.py").read_text()
        self.assertIn(f"Eclipse Muster MCP — v{version}", server_py)
        self.assertIn(f'"version": "{version}"', server_py)
        readme = (self.ROOT / "README.md").read_text()
        self.assertIn(f"v{version}, phase", readme)
        manifest = json.loads((self.ROOT / "bazaar-manifest.json")
                              .read_text())
        self.assertIn(f"v{version}", manifest["status"])
        # initialize handshake reports the same version.
        resp = S.handle({"method": "initialize", "id": 1,
                         "params": {}})
        self.assertEqual(
            resp["result"]["serverInfo"]["version"], version)


class TestP2Disclosure(unittest.TestCase):
    """QA-PATTERNS P2 / MUSTER-10: every certification / scope
    sentence in the repo's disclosure surfaces must agree with the
    others and with the build. Inventory: README.md, PYPI_README.md,
    bazaar-manifest.json."""

    ROOT = Path(__file__).parent.parent

    def _texts(self):
        return {
            "README": (self.ROOT / "README.md").read_text(),
            "PYPI_README": (self.ROOT / "PYPI_README.md").read_text(),
            "manifest": (self.ROOT / "bazaar-manifest.json")
            .read_text(),
        }

    def test_no_certification_claimed_anywhere(self):
        for name, text in self._texts().items():
            low = text.lower()
            self.assertNotIn("certified (local)", low, name)
            self.assertNotIn("certified(local)", low, name)
            self.assertIn("no certification", low, name)
        manifest = json.loads(self._texts()["manifest"])
        self.assertFalse(manifest["bench"]["certified"])
        self.assertIn("no certification",
                      manifest["bench"]["bench_verdict"].lower())
        self.assertIn("no certification",
                      manifest["bench"]["status"].lower())
        self.assertIn("no certification",
                      manifest["status"].lower())

    def test_c1_loaders_disclosed_not_c0_only(self):
        for name in ("README", "PYPI_README"):
            text = self._texts()[name]
            self.assertIn("c1_loader", text, name)
            self.assertNotIn("synthetic fixtures only", text, name)


class TestP6IntegrityWarning(unittest.TestCase):
    """QA-PATTERNS P6 / MUSTER-13: a tampered receipt must surface a
    banner-class warning NAMING the divergence seq at the point of
    use (receipt_list) — not a bare bool."""

    def test_tamper_names_seq_in_banner(self):
        state = ingest_all(fresh_state())
        S.t_record_search(state, {"kind": "person", "query": "maya"})
        clean = S.t_receipt_list(state, {})
        self.assertTrue(clean["chain_valid"])
        self.assertNotIn("integrity_warning", clean)
        target = state["receipts"][2]
        target["detail"]["verdict"] = "FORGED"
        out = S.t_receipt_list(state, {})
        self.assertFalse(out["chain_valid"])
        self.assertEqual(out["chain_divergence_seq"], target["seq"])
        warning = out["integrity_warning"]
        self.assertEqual(warning["class"], "banner")
        self.assertEqual(warning["severity"], "critical")
        self.assertIn(str(target["seq"]), warning["message"])
        self.assertIn("INTEGRITY WARNING", warning["message"])

    def test_linkage_break_names_following_seq(self):
        state = ingest_all(fresh_state())
        state["receipts"][1]["prev_hash"] = "FORGED"
        detail = S.verify_receipt_chain_detail(state["receipts"])
        self.assertFalse(detail["valid"])
        self.assertEqual(detail["divergence_seq"],
                         state["receipts"][1]["seq"])


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
        # 10 since MUSTER-14 added the confirm-gated retention_sweep
        # (the P7 erasure-class operation); the denylist is unchanged.
        self.assertEqual(len(names), 10)
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
                # MERGE ADAPTATION (2026-11-16 union merge): was 9 at
                # 0.1.0; 0.1.1 serves 10 — MUSTER-14 added the
                # confirm-gated retention_sweep. Count updated to the
                # 0.1.1 truth; the equality-to-served-registry check
                # above is unchanged and unweakened.
                self.assertEqual(len(names), 10)
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
    ($ECLIPSE_MUSTER_STATE override, else the per-user data root —
    MUSTER-21 moved the default off ./data/state.json beside
    server.py; documented in the module docstring). Exercising the
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
        # MERGE ADAPTATION (2026-11-16, second union merge): was
        # ./data/state.json beside server.py at 0.1.0; MUSTER-21
        # (origin line) moved the default to the per-user data root.
        # Assertion updated to the new documented truth — name still
        # state.json, parent is the eclipse-muster per-user dir, and
        # the path is never beside server.py (same fact origin's
        # TestStatePath.test_default_is_never_beside_server asserts).
        self.assertEqual(S.STATE_PATH.name, "state.json")
        self.assertEqual(S.STATE_PATH.parent.name, "eclipse-muster")
        self.assertNotEqual(S.STATE_PATH.parent,
                            Path(__file__).parent.parent / "data")


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


class TestP15StoredContentCensus(unittest.TestCase):
    """P15 (MCP face): stored-content census vs disclosure + token
    custody. Every stored field class per record kind is enumerated
    and diffed against the disclosed inventory; receipt detail keys
    likewise; the owner-confirm token is derived, never stored."""

    RECORD_KEYS = {"record_id", "kind", "fields", "external_ids",
                   "data_rights", "status", "ingested_at"}
    # Disclosed stored field classes per kind (union over the C0
    # fixture corpus). ssn_last4_note is a stored person field class
    # whose egress is null on every surface (masking test, spec 5.5)
    # — the census enumerates it; it is not an undisclosed class.
    FIELD_CLASSES = {
        "person": {"emails", "name", "phone", "role",
                   "ssn_last4_note", "timezone"},
        "company": {"domains", "industry", "name"},
        "ticket": {"priority", "status", "subject"},
        "conversation": {"messages"},
        "activity": set(),
    }
    RECEIPT_DETAIL_KEYS = {
        "record_ingested": {"field_count", "fields_hash", "kind",
                            "quarantined_message_ids", "record_id",
                            "tier", "verdict"},
        "record_read": {"kind", "record_id", "surface", "tier",
                        "verdict"},
        "record_search": {"hit_count", "kind", "query_hash", "tier",
                          "verdict"},
        "merge_proposed": {"proposal_hash", "proposal_id",
                           "record_ids", "tier", "verdict"},
        "merge_execute_denied": {"proposal_id", "reason", "tier",
                                 "verdict"},
        "merge_executed": {"merged_record_id", "merge_id",
                           "proposal_id", "tier", "verdict"},
        "merge_unmerge_denied": {"merge_id", "tier", "verdict"},
        "merge_unmerged": {"merge_id", "restored_record_ids",
                           "tier", "verdict"},
        "draft_created": {"draft_id", "missing_facts",
                          "quarantine_flagged", "ticket_id", "tier",
                          "verdict"},
    }

    def test_record_census_matches_disclosure(self):
        state = ingest_all(fresh_state())
        ingested = {r["record_id"]: set(r["fields"].keys())
                    for r in FIXTURES}
        for rec in state["records"].values():
            self.assertLessEqual(set(rec.keys()), self.RECORD_KEYS,
                                 rec["record_id"])
            # No stored class beyond the disclosed per-kind union,
            # and no class invented or dropped vs what was ingested.
            self.assertLessEqual(set(rec["fields"].keys()),
                                 self.FIELD_CLASSES[rec["kind"]],
                                 rec["record_id"])
            self.assertEqual(set(rec["fields"].keys()),
                             ingested[rec["record_id"]],
                             rec["record_id"])
            for entry in rec["fields"].values():
                self.assertIn("value", entry)
                self.assertIn("provenance", entry)

    def test_receipt_detail_census(self):
        state = ingest_all(fresh_state())
        S.t_record_read(state, {"record_id": "person-hubspot-1001",
                                "surface": "analytic"})
        S.t_record_search(state, {"kind": "person", "query": "maya"})
        seen = {r["type"] for r in state["receipts"]}
        for r in state["receipts"]:
            self.assertIn(r["type"], self.RECEIPT_DETAIL_KEYS)
            self.assertLessEqual(set(r["detail"].keys()),
                                 self.RECEIPT_DETAIL_KEYS[r["type"]],
                                 r["type"])
        self.assertLessEqual(
            {"record_ingested", "record_read", "record_search"}, seen)

    def test_confirm_token_never_stored(self):
        state = ingest_all(fresh_state())
        out = S.t_merge_propose(state, {
            "record_a": "person-hubspot-1001",
            "record_b": "person-zendesk-2002"})
        token = S.expected_merge_token(out["proposal"])
        S.t_merge_execute(state, {
            "proposal_id": out["proposal"]["proposal_id"],
            "confirm_token": token})
        blob = json.dumps(state)
        self.assertNotIn(token, blob)
        self.assertNotIn("confirm-merge-", blob)
        self.assertNotIn("confirm-unmerge-", blob)


def find_real_graph_data(root):
    """MUSTER-22 guard: real (non-fixture) graph state must never sit
    in the source tree. Returns a list of offending paths under root.

    A file offends when (a) its name marks it as a C1 / firm-graph
    store, (b) it lives under a tree-resident data/ directory, or
    (c) it parses as a Muster state document (records + proposals +
    merges + receipts) carrying any records at all. The shipped
    synthetic C0 fixture file (fixtures/records.json, a bare
    {"records": [...]} list document) matches none of these.
    """
    import os
    offenders = []
    state_keys = {"records", "proposals", "merges", "receipts"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__")]
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root)
            if name.startswith("state-c1") or "firm-graph" in name:
                offenders.append(str(rel))
                continue
            if "data" in rel.parts[:-1] and name.endswith(".json"):
                offenders.append(str(rel))
                continue
            if name.endswith(".json"):
                try:
                    doc = json.loads(path.read_text())
                except Exception:
                    continue
                if (isinstance(doc, dict) and state_keys <= set(doc)
                        and isinstance(doc.get("records"), dict)
                        and doc["records"]):
                    offenders.append(str(rel))
    return offenders


class TestTreeHygieneGuard(unittest.TestCase):
    """MUSTER-22 (K2): the C1 firm graph sat untracked in this tree
    (data/state-c1-firm-graph.json, 218 real records) while the
    shipped wheel stayed clean only by force-include luck. The store
    now lives in the estate live-store root outside the tree; this
    guard fails if real graph data ever reappears in the tree."""

    def test_tree_carries_no_real_graph_data(self):
        root = Path(__file__).parent.parent
        self.assertEqual(find_real_graph_data(root), [])

    def test_guard_catches_planted_c1_store(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            planted = root / "data" / "state-c1-firm-graph.json"
            planted.parent.mkdir(parents=True)
            planted.write_text(json.dumps({
                "records": {"person-real": {"kind": "person"}},
                "proposals": {}, "merges": {}, "drafts": {},
                "receipts": [{"seq": 1}]}))
            self.assertEqual(find_real_graph_data(root),
                             ["data/state-c1-firm-graph.json"])

    def test_guard_catches_state_document_under_any_name(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "backup.json").write_text(json.dumps({
                "records": {"person-real": {"kind": "person"}},
                "proposals": {}, "merges": {}, "drafts": {},
                "receipts": []}))
            self.assertEqual(find_real_graph_data(root), ["backup.json"])

    def test_guard_allows_fixtures_and_manifests(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "fixtures").mkdir()
            (root / "fixtures" / "records.json").write_text(
                (Path(__file__).parent.parent / "fixtures"
                 / "records.json").read_text())
            (root / "policy.json").write_text("{}")
            self.assertEqual(find_real_graph_data(root), [])


class TestStatePath(unittest.TestCase):
    """MUSTER-21 (K5 / QA P12): the default state path used to resolve
    to data/state.json beside server.py — runtime state written into
    the package/install location. The default now resolves to the
    per-user data root on every platform; the explicit
    ECLIPSE_MUSTER_STATE override still wins. (The earlier test-gap
    TestP12StatePath proved only the env-set path; the default path
    is covered here for the first time.)"""

    def _default_with(self, platform, env):
        import os
        from unittest import mock
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("ECLIPSE_MUSTER_STATE", "XDG_DATA_HOME",
                              "LOCALAPPDATA")}
        clean.update(env)
        with mock.patch.object(sys, "platform", platform), \
                mock.patch.dict(os.environ, clean, clear=True):
            return S._default_state_path()

    def test_default_linux_uses_xdg_data_home(self):
        got = self._default_with("linux", {"XDG_DATA_HOME": "/tmp/xdg"})
        self.assertEqual(got, Path("/tmp/xdg/eclipse-muster/state.json"))

    def test_default_linux_falls_back_to_home_share(self):
        got = self._default_with("linux", {})
        self.assertEqual(got, Path.home() / ".local" / "share"
                         / "eclipse-muster" / "state.json")

    def test_default_windows_uses_localappdata(self):
        got = self._default_with(
            "win32", {"LOCALAPPDATA": "C:/Users/x/AppData/Local"})
        self.assertEqual(got, Path("C:/Users/x/AppData/Local")
                         / "EclipseMuster" / "state.json")

    def test_default_macos_uses_application_support(self):
        got = self._default_with("darwin", {})
        self.assertEqual(got, Path.home() / "Library"
                         / "Application Support" / "EclipseMuster"
                         / "state.json")

    def test_default_is_never_beside_server(self):
        for platform in ("linux", "win32", "darwin"):
            got = self._default_with(platform, {})
            self.assertNotEqual(got.parent, S.HERE)
            self.assertNotEqual(got, S.HERE / "data" / "state.json")

    def test_override_wins_on_every_platform(self):
        for platform in ("linux", "win32", "darwin"):
            got = self._default_with(
                platform, {"ECLIPSE_MUSTER_STATE": "/estate/store.json"})
            self.assertEqual(got, Path("/estate/store.json"))

    def _run_server(self, env_extra, payload):
        import os
        import subprocess
        env = {k: v for k, v in os.environ.items()
               if k != "ECLIPSE_MUSTER_STATE"}
        env.update(env_extra)
        proc = subprocess.Popen(
            [sys.executable, str(S.HERE / "server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, cwd=str(S.HERE), env=env)
        try:
            def call(req):
                proc.stdin.write(json.dumps(req) + "\n")
                proc.stdin.flush()
                return json.loads(proc.stdout.readline())
            call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-03-26"}})
            return call({"jsonrpc": "2.0", "id": 2,
                         "method": "tools/call",
                         "params": {"name": "record_ingest",
                                    "arguments": payload}})
        finally:
            proc.stdin.close()
            proc.wait(timeout=30)
            proc.stdout.close()

    def test_fresh_process_default_writes_only_to_per_user_root(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            resp = self._run_server({"XDG_DATA_HOME": td}, FIXTURES[0])
            self.assertNotIn("error", resp)
            written = Path(td) / "eclipse-muster" / "state.json"
            self.assertTrue(written.exists())
            self.assertFalse((S.HERE / "data" / "state.json").exists())

    def test_fresh_process_override_wins_and_writes_only_there(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "custom" / "state-c1-check.json"
            resp = self._run_server(
                {"ECLIPSE_MUSTER_STATE": str(target),
                 "XDG_DATA_HOME": str(Path(td) / "xdg")}, FIXTURES[0])
            self.assertNotIn("error", resp)
            self.assertTrue(target.exists())
            self.assertFalse(
                (Path(td) / "xdg" / "eclipse-muster"
                 / "state.json").exists())
            self.assertFalse((S.HERE / "data" / "state.json").exists())


if __name__ == "__main__":
    unittest.main()

# Eclipse Muster MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
