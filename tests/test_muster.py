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


if __name__ == "__main__":
    unittest.main()

# Eclipse Muster MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
