#!/usr/bin/env python3
# PROPRIETARY — CYAN ECLIPSE TECHNOLOGIES LLC · COMPANY INTELLECTUAL PROPERTY
# Eclipse Muster MCP — mailbox ingest leg (tools/mail_ingest.py)
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
"""(IP — PROPRIETARY IP) Mailbox ingest leg for Cyan Eclipse Muster.

Ring structure (PLAN muster-windows-face-20261121 §2/§3): the leg
takes a mailbox SOURCE as config — an export directory of .eml files
or a JSONL export, plus a mailbox label and the mailbox owner's
address. Ring 1 is synthetic fixtures only. Ring 2 (a real M365
mailbox) is a later sign-in/export step on the owner's machine that
produces the same export format; this module never touches a real
mailbox, a network, or a credential.

Shape: messages -> canonical record_ingest payloads (one
conversation record per thread + one person record per
correspondent) written as a tenant export file, then imported ONLY
through tenant_store.py's governed import machinery (Session +
record_ingest). This module never writes the store file directly.

Posture:
  * Provenance on every record/field: source mailbox label, folder,
    Message-ID (platform_object_id), Data Rights Ledger block per
    record, exactly as record_ingest requires.
  * Dedupe: Message-ID exact; fallback content hash over (sender,
    recipients, subject, date, body) for messages without one.
    Existing records are checked via record_search on the
    deterministic record_id before ingest, so re-importing the same
    export adds 0 records.
  * Person-data: a body carrying gov_id-class (SSN-shaped),
    full-DOB-class, or residential-address material quarantines the
    WHOLE conversation record — it is never ingested, and a
    quarantined message contributes no person records either.
  * Instruction-shaped customer text is NOT pre-filtered here: it is
    fed to record_ingest as author="customer", so the server's own
    quarantine path tags it untrusted + quarantined. Correspondents
    are customers; the mailbox owner is author="agent".
  * Batch receipt: chain verified before and after import
    (tenant_store verify path); an import report JSON (counts by
    disposition + export sha256 + chain head before/after) is
    written beside the export.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import tenant_store as T  # noqa: E402

ADAPTER_VERSION = "mail-ingest-0.2.0-pilot.1"
PLATFORM = "mailbox-export"

# --- Class-C detectors (whole-record quarantine) -------------------
_SSN_SHAPE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_DOB_LABEL = re.compile(
    r"(date of birth|d\.?o\.?b\.?|born on)[^.\n]{0,20}?"
    r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}", re.I)
_DOB_ISO = re.compile(
    r"(date of birth|d\.?o\.?b\.?|born on)[^.\n]{0,20}?"
    r"\d{4}-\d{2}-\d{2}", re.I)
_RESIDENTIAL = re.compile(
    r"(home address|residential address|i live at|my address is)"
    r"[^.\n]{0,80}\d{1,6}\s+\w+", re.I)
_STREET = re.compile(
    r"\b\d{1,6}\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}\s+"
    r"(Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Court|Ct|"
    r"Boulevard|Blvd)\b[^\n]{0,40}\b\d{5}(-\d{4})?\b")


def class_c_hits(text: str) -> list:
    """Names of Class-C material classes present in a body."""
    hits = []
    if _SSN_SHAPE.search(text):
        hits.append("gov_id_shape")
    if _DOB_LABEL.search(text) or _DOB_ISO.search(text):
        hits.append("full_dob")
    if _RESIDENTIAL.search(text) or _STREET.search(text):
        hits.append("residential_address")
    return hits


def content_hash(msg: dict) -> str:
    basis = json.dumps({
        "from": msg["from"]["email"].lower(),
        "to": sorted(a["email"].lower() for a in msg["to"]),
        "subject": msg["subject"], "date": msg["date"],
        "body": msg["body"]}, sort_keys=True)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _addr_pairs(value: str) -> list:
    out = []
    for name, email_addr in getaddresses([value or ""]):
        if email_addr:
            out.append({"name": name or email_addr.split("@")[0],
                        "email": email_addr})
    return out


def _iso_date(raw: str) -> str:
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return raw or ""


def parse_eml_dir(source: Path, folder: str = "Inbox") -> list:
    msgs = []
    for path in sorted(source.glob("*.eml")):
        m = BytesParser(policy=policy.default).parsebytes(
            path.read_bytes())
        body_part = m.get_body(preferencelist=("plain"))
        if body_part is not None:
            body = body_part.get_content()
        else:
            payload = m.get_payload(decode=True) or b""
            body = payload.decode("utf-8", "replace")
        refs = (m.get("References") or "").split()
        msgs.append({
            "message_id": (m.get("Message-ID") or "").strip() or None,
            "folder": folder,
            "from": (_addr_pairs(m.get("From") or "")
                     or [{"name": "", "email": ""}])[0],
            "to": _addr_pairs(m.get("To") or ""),
            "cc": _addr_pairs(m.get("Cc") or ""),
            "subject": str(m.get("Subject") or ""),
            "date": _iso_date(str(m.get("Date") or "")),
            "body": body.strip(),
            "in_reply_to": (m.get("In-Reply-To") or "").strip()
                           or None,
            "references": refs,
            "origin_file": path.name})
    return msgs


def parse_jsonl(source: Path) -> list:
    msgs = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        msgs.append({
            "message_id": row.get("message_id") or None,
            "folder": row.get("folder", "Inbox"),
            "from": row["from"], "to": row.get("to", []),
            "cc": row.get("cc", []),
            "subject": row.get("subject", ""),
            "date": row.get("date", ""), "body": row.get("body", ""),
            "in_reply_to": row.get("in_reply_to"),
            "references": row.get("references", []),
            "origin_file": source.name})
    return msgs


def load_messages(source: Path) -> list:
    if source.is_dir():
        return parse_eml_dir(source)
    if source.suffix == ".jsonl":
        return parse_jsonl(source)
    raise SystemExit(f"unsupported mailbox source: {source} "
                     f"(expected a .eml directory or a .jsonl file)")


def _norm_subject(subject: str) -> str:
    s = subject.strip()
    while re.match(r"(?i)(re|fwd|fw)\s*:\s*", s):
        s = re.sub(r"(?i)^(re|fwd|fw)\s*:\s*", "", s).strip()
    return s.lower()


def _thread_key(msg: dict) -> str:
    if msg["references"]:
        return msg["references"][0]
    if msg["in_reply_to"]:
        return msg["in_reply_to"]
    if msg["message_id"]:
        return msg["message_id"]
    return "subj:" + _norm_subject(msg["subject"]) + "|" + ",".join(
        sorted([msg["from"]["email"].lower()]
               + [a["email"].lower() for a in msg["to"]]))


def _prov(obj_id: str, observed_at: str) -> dict:
    return {"platform": PLATFORM, "platform_object_id": obj_id,
            "source": "import", "observed_at": observed_at,
            "adapter_version": ADAPTER_VERSION}


def _drl(label: str) -> dict:
    return {"source_account": label,
            "legal_basis": ("mailbox owner instruction — "
                            "owner-triggered, read-only export "
                            "ingest (Ring 1: synthetic fixtures)"),
            "method": "mail_ingest", "retention": "tenant-persistent",
            "pii_class": "strictest"}


def build_payloads(messages: list, label: str, owner_email: str):
    """Messages -> (payloads, stats). Dedupe + Class-C quarantine
    happen here; instruction-shaped text passes through to the
    server's quarantine path untouched."""
    owner = owner_email.lower()
    stats = {"messages_seen": len(messages), "duplicate_messages": 0,
             "quarantined_records": 0, "quarantined_messages": 0,
             "class_c_classes": {}}
    seen_ids, seen_hashes, kept = set(), set(), []
    for msg in messages:
        h = content_hash(msg)
        if msg["message_id"] and msg["message_id"] in seen_ids:
            stats["duplicate_messages"] += 1
            continue
        if not msg["message_id"] and h in seen_hashes:
            stats["duplicate_messages"] += 1
            continue
        if msg["message_id"]:
            seen_ids.add(msg["message_id"])
        seen_hashes.add(h)
        msg = dict(msg, _hash=h)
        kept.append(msg)

    threads = {}
    for msg in kept:
        threads.setdefault(_thread_key(msg), []).append(msg)

    payloads, persons = [], {}
    for key, thread in sorted(threads.items()):
        thread.sort(key=lambda m: m["date"])
        hits = {}
        for msg in thread:
            for cls in class_c_hits(msg["body"] + "\n"
                                    + msg["subject"]):
                hits[cls] = hits.get(cls, 0) + 1
        if hits:
            stats["quarantined_records"] += 1
            stats["quarantined_messages"] += len(thread)
            for cls, n in hits.items():
                stats["class_c_classes"][cls] = \
                    stats["class_c_classes"].get(cls, 0) + n
            continue
        conv_msgs, participants = [], []
        for msg in thread:
            author = ("agent" if msg["from"]["email"].lower() == owner
                      else "customer")
            conv_msgs.append({
                "author": author,
                "message_id": msg["message_id"] or f"hash:{msg['_hash'][:16]}",
                "ts": msg["date"], "body": msg["body"]})
            for party in [msg["from"]] + msg["to"] + msg["cc"]:
                em = party["email"].lower()
                if em and em != owner and em not in participants:
                    participants.append(em)
                if em and em != owner:
                    persons.setdefault(em, (party, msg["date"]))
        root = thread[0]
        rid = "conv-mail-" + hashlib.sha256(
            key.encode()).hexdigest()[:10]
        fields = {
            "subject": root["subject"],
            "participants": participants,
            "messages": conv_msgs,
            "source_mailbox": label,
            "folder": root["folder"],
            "message_ids": [m["message_id"] or f"hash:{m['_hash'][:16]}"
                            for m in thread]}
        payloads.append({
            "kind": "conversation", "record_id": rid,
            "external_ids": {"mail_thread": key,
                             "mailbox": label},
            "fields": fields,
            "field_provenance": {
                f: _prov(key, root["date"]) for f in fields},
            "data_rights": _drl(label)})
    for em, (party, first_seen) in sorted(persons.items()):
        rid = "person-mail-" + hashlib.sha256(
            em.encode()).hexdigest()[:10]
        fields = {"name": party["name"], "emails": [em],
                  "role": "mail correspondent"}
        payloads.append({
            "kind": "person", "record_id": rid,
            "external_ids": {"email": em, "mailbox": label},
            "fields": fields,
            "field_provenance": {
                f: _prov(em, first_seen) for f in fields},
            "data_rights": _drl(label)})
    stats["payload_records"] = len(payloads)
    return payloads, stats


def _chain_head(server_py: Path, state: Path):
    session = T.Session(server_py, state)
    try:
        out, is_err = session.tool("receipt_list", {"limit": 1})
        if is_err:
            raise SystemExit(f"receipt_list failed: {out}")
        head = None
        if out["receipts"]:
            head = out["receipts"][-1].get("hash")
        return {"chain_valid": out["chain_valid"],
                "receipt_count": out["count"], "chain_head": head}
    finally:
        session.close()


def _existing_ids(server_py: Path, state: Path, payloads: list):
    session = T.Session(server_py, state)
    try:
        existing = set()
        for payload in payloads:
            out, is_err = session.tool(
                "record_search",
                {"kind": payload["kind"], "query": payload["record_id"],
                 "surface": "analytic"})
            if not is_err and any(
                    h["record_id"] == payload["record_id"]
                    for h in out["hits"]):
                existing.add(payload["record_id"])
        return existing
    finally:
        session.close()


def do_mail_import(server_py: Path, state: Path, source: Path,
                   label: str, owner_email: str,
                   export_out: Path) -> dict:
    if not state.exists():
        raise SystemExit(f"store does not exist: {state} "
                         f"(run tenant_store init first)")
    before = T.do_verify(server_py, state)
    if not before["chain_valid"]:
        raise SystemExit("chain verify FAILED before import — "
                         "import refused (fails closed)")
    head_before = _chain_head(server_py, state)

    messages = load_messages(source)
    payloads, stats = build_payloads(messages, label, owner_email)
    export_doc = {"_comment": (f"mail ingest export — mailbox "
                               f"'{label}' via tools/mail_ingest.py; "
                               f"synthetic fixtures only in Ring 1"),
                  "records": payloads}
    export_out.parent.mkdir(parents=True, exist_ok=True)
    export_out.write_text(json.dumps(export_doc, indent=1),
                          encoding="utf-8")
    export_sha = hashlib.sha256(export_out.read_bytes()).hexdigest()

    existing = _existing_ids(server_py, state, payloads)
    fresh = [p for p in payloads if p["record_id"] not in existing]
    report_counts = {"added": 0, "duplicate": len(existing),
                     "quarantined": stats["quarantined_records"],
                     "rejected": 0}
    import_report = None
    if fresh:
        fresh_path = export_out.with_name(
            export_out.stem + ".fresh.json")
        fresh_path.write_text(json.dumps({"records": fresh}, indent=1),
                              encoding="utf-8")
        import_report = T.do_import(server_py, state, fresh_path)
        fresh_path.unlink()
        report_counts["added"] = import_report["ingested"]
        report_counts["rejected"] = len(import_report["rejects"])

    after = T.do_verify(server_py, state)
    head_after = _chain_head(server_py, state)
    report = {
        "mailbox_label": label, "source": str(source),
        "export_path": str(export_out), "export_sha256": export_sha,
        "messages_seen": stats["messages_seen"],
        "duplicate_messages": stats["duplicate_messages"],
        "quarantined_messages": stats["quarantined_messages"],
        "class_c_classes": stats["class_c_classes"],
        "counts": report_counts,
        "server_rejects": (import_report or {}).get("rejects", []),
        "chain_before": {**head_before,
                         "total_records": before["total_records"]},
        "chain_after": {**head_after,
                        "total_records": after["total_records"]},
        "chain_valid_after": after["chain_valid"]}
    report_path = export_out.with_name(
        export_out.stem + ".import-report.json")
    report_path.write_text(json.dumps(report, indent=1),
                           encoding="utf-8")
    report["report_path"] = str(report_path)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import")
    p.add_argument("--state", required=True)
    p.add_argument("--source", required=True,
                   help=".eml export directory or .jsonl export file")
    p.add_argument("--label", required=True,
                   help="source mailbox label (provenance)")
    p.add_argument("--owner-email", required=True,
                   help="mailbox owner's address (author=agent)")
    p.add_argument("--export-out", required=True)
    p.add_argument("--server", default=str(T.DEFAULT_SERVER))
    args = ap.parse_args(argv)
    if args.cmd == "import":
        report = do_mail_import(
            Path(args.server), Path(args.state), Path(args.source),
            args.label, args.owner_email, Path(args.export_out))
        print(json.dumps(report, indent=1))
        return 0 if report["chain_valid_after"] else 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
