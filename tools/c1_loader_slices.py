#!/usr/bin/env python3
# Eclipse Muster MCP — C1 loader, slices 2-4 (tools/c1_loader_slices.py)
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
"""C1 real-data loader, slices 2-4, for the firm's own customer graph.

Sibling of tools/c1_loader.py (slice 1, relationship map). Same
doctrine, extended sources (SOURCES.md S-3, S-4, S-5):

  slice2  leads — the intel-enriched lead set from the leadgen store
          (leads.json entries carrying an intel block) as Deal +
          Company records, and the owner disposition events from
          DISPOSITIONS-20261110/11.md as Activity records. The raw
          scan pool (1,102 unscored leads) is NOT graph material and
          is never ingested. DERIVED intel bands carry an estimate
          marker field, per SOURCES.md S-3.
  slice3  P-6 outreach — the 12 prep-book accounts as Company
          records, the prep's first-party named individuals as
          Person records, and the SENDS outcomes as Activity records.
          The prep's never-use addresses (a third-party Gmail claim
          for Desktop Commander; partner@supabase.io) are excluded
          by construction and asserted absent by the tests. Names
          the prep itself flags as aggregator-only or weakly sourced
          (Exa, Brave) are NOT ingested as persons.
  slice4  bids — every contracts-deadline-watch register row as a
          Deal record (live / waiting / closed classes kept), live-
          row agencies as Company records, points of contact printed
          in packet SEND-RECORDs (and the FISMA / APEX / E-ICAM /
          AMC-A6 records of the same standing) as Person records,
          and send events as Activity records with their evidence
          class (SENT-UNVERIFIED / DELIVERED-EVIDENCED / BOUNCED /
          NOT SENT) carried per record, never flattened.

Dedupe discipline: a person already in the slice-1 store is never
re-ingested. The slice-4 extractor takes the slice-1 person roster
and diverts a match to an identity-link Activity record carrying
the new source and any new structured datum, so the graph holds
exactly one record per person and the collision is on the chain.

Extraction is from structured records only: register rows, store
entries, prep-book sections, SEND-RECORD To/state lines. No email
bodies are read; a field with no source stays absent.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from c1_loader import Session, guard_state_path  # noqa: E402

ADAPTER_VERSION = "c1-loader-1.1.0"
OBSERVED_AT = "2026-11-13T00:00:00Z"

DATA_RIGHTS = {
    "source_account": "cyan-eclipse-owner-records",
    "legal_basis": "account-owner instruction (C1 phase ruling "
                   "2026-11-13; slices 2-4 build directive "
                   "2026-11-13)",
    "method": "c1_loader_slices",
    "retention": "in-estate persistent (owner records)",
    "pii_class": "strictest",
}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
EVIDENCE_RE = re.compile(
    r"DELIVERED-EVIDENCED|SENT-UNVERIFIED|BOUNCED|NOT SENT")


def slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > limit:
        s = s[:limit].rstrip("-") + "-" + hashlib.sha256(
            text.encode()).hexdigest()[:6]
    return s


def payload(kind, record_id, platform, object_id, fields,
            external_ids=None):
    prov = {"platform": platform, "platform_object_id": object_id,
            "source": "import", "observed_at": OBSERVED_AT,
            "adapter_version": ADAPTER_VERSION}
    return {
        "kind": kind,
        "record_id": record_id,
        "external_ids": external_ids or {},
        "fields": fields,
        "field_provenance": {f: dict(prov) for f in fields},
        "data_rights": dict(DATA_RIGHTS),
    }


# ------------------------------------------------------------------
# Slice 2 — leads (S-3)
# ------------------------------------------------------------------

def extract_slice2(leads_json: Path) -> list:
    store = json.loads(leads_json.read_text(encoding="utf-8"))
    leads = store["leads"]
    intel = {k: v for k, v in leads.items() if v.get("intel")}
    # Dedupe: the same solicitation posted twice (the W50NH9 GEOINT
    # pair) is one Deal carrying both store keys.
    groups = {}
    for key, lead in intel.items():
        gk = lead.get("sol_number") or lead["title"]
        groups.setdefault(gk, []).append((key, lead))
    out = []
    agencies = {}
    for gk, members in groups.items():
        key, lead = members[0]
        fields = {
            "name": lead["title"],
            "agency": lead["agency"],
            "stage": "prospect - intel-enriched (leadgen store)",
            "source_url": lead["link"],
            "first_seen": lead["first_seen"],
            "last_seen": lead["last_seen"],
        }
        for src, dst in (("notice_id", "notice_id"),
                         ("sol_number", "solicitation_number"),
                         ("type", "notice_type"),
                         ("posted", "posted"),
                         ("deadline", "deadline"),
                         ("naics", "naics"), ("psc", "psc"),
                         ("set_aside_desc", "set_aside")):
            if lead.get(src):
                fields[dst] = lead[src]
        if lead.get("est_value") is not None:
            fields["est_value_usd"] = lead["est_value"]
            fields["est_value_basis"] = (
                "as published in the source notice (leadgen store)")
        intelblk = lead["intel"]
        fields["intel_awards_examined"] = intelblk.get(
            "awards_examined", 0)
        incumbents = [i["recipient"]
                      for i in intelblk.get("incumbents", [])]
        if incumbents:
            fields["intel_incumbents"] = incumbents
        band = intelblk.get("implied_annual_band_usd_DERIVED") or {}
        if band.get("n"):
            fields["intel_derived_band_usd"] = band
            fields["intel_band_basis"] = (
                "DERIVED by pricing-intel from USAspending award "
                "history; derived market data, not an awarded, "
                "offered, or quoted value")
        keys = [k for k, _ in members]
        out.append(payload(
            "deal", f"deal-lead-{slug(key.split(':')[-1], 40)}",
            "cyan-eclipse-leadgen", key, fields,
            {"leadgen_keys": keys}))
        agencies.setdefault(lead["agency"], key)
    for agency, key in agencies.items():
        out.append(payload(
            "company", f"company-leadgen-{slug(agency, 40)}",
            "cyan-eclipse-leadgen", key,
            {"name": agency,
             "role": "prospect agency (leadgen intel set)"},
            {"leadgen_agency": agency}))
    out.extend(_disposition_activities())
    return out


def _disposition_activities() -> list:
    # Owner disposition events of record, DISPOSITIONS-20261110.md
    # and DISPOSITIONS-20261111.md (leads/). Each entry: (date,
    # ruling, subject, deal_ref, source file). The AMC/A6 register
    # event appears in both files; the 2026-11-12 record (fuller,
    # register row created 2026-11-12) is the one ingested.
    events = [
        ("2026-11-10", "PASS (owner ruling)",
         "LA-RICS Telecommunication Project Management and General "
         "Consulting Services",
         "lead bidnet:444224841385", "DISPOSITIONS-20261110.md"),
        ("2026-11-10", "PURSUIT REGISTERED (owner ruling)",
         "NGA Mercury Draft Solicitation",
         "register nga-mercury", "DISPOSITIONS-20261110.md"),
        ("2026-11-11", "PURSUE - pursuit registered (owner tap)",
         "WHS IMPROVE Sources Sought",
         "register whs-improve", "DISPOSITIONS-20261111.md"),
        ("2026-11-11", "PURSUE - pursuit registered (owner tap)",
         "USTRANSCOM CSO AoI 003 Single Mobility System (SMS)",
         "register ustranscom-sms-aoi", "DISPOSITIONS-20261111.md"),
        ("2026-11-12", "PASS (owner ruling)",
         "FEMA Direct Housing Modernization RFI",
         "register fema-direct-housing-rfi",
         "DISPOSITIONS-20261111.md"),
        ("2026-11-12", "PURSUIT REGISTERED (owner ruling)",
         "AMC/A6 Information Technology Needs CSO, Amendment 04",
         "register amc-a6-cso", "DISPOSITIONS-20261111.md"),
    ]
    out = []
    for date, ruling, subject, ref, src in events:
        oid = slug(subject, 40)
        out.append(payload(
            "activity", f"activity-disp-{oid}",
            "cyan-eclipse-leadgen", oid,
            {"event": f"{ruling}: {subject}",
             "event_date": date,
             "deal_ref": ref,
             "source_ref": f"leads/{src}"},
            {"disposition": oid}))
    return out


# ------------------------------------------------------------------
# Slice 3 — P-6 outreach (S-4)
# ------------------------------------------------------------------

# (org, product, tier, route status, published route or None)
P6_COMPANIES = [
    ("GitHub", "GitHub MCP Server", "Tier 1",
     "FOUND (named + verifiable path)", "partnerships@github.com"),
    ("Microsoft", "Playwright MCP", "Tier 1",
     "FOUND (named + verifiable path; no published direct email)",
     None),
    ("Upstash", "Context7", "Tier 1",
     "FOUND as a channel (first-party published)",
     "context7@upstash.com"),
    ("Firecrawl", "Firecrawl MCP", "Tier 1",
     "FOUND (named + verifiable path)", "partnerships@firecrawl.dev"),
    ("Desktop Commander", "Desktop Commander MCP", "Tier 1",
     "FOUND (named maintainer + verifiable path; route runs "
     "maintainer-direct via the GitHub repo)", None),
    ("Anthropic, PBC", "mcp-server-fetch (MCP reference servers)",
     "Tier 1",
     "FOUND (named maintainer of record + verifiable repo path; "
     "alternate C, promoted 2026-11-12 into the struck slot)", None),
    ("Sentry", "Sentry MCP", "Tier 2",
     "FOUND (named + verifiable path)",
     "partnership-platform@sentry.io"),
    ("Exa", "Exa MCP", "Tier 2",
     "FOUND as a channel (first-party published)",
     "partnerships@exa.ai"),
    ("Brave", "Brave Search MCP Server", "Tier 3",
     "FOUND (published path)", "bizdev@brave.com"),
    ("Supabase", "Supabase MCP Server", "Tier 3",
     "FOUND as a channel (first-party program: Partner Program "
     "application at supabase.com/partners)", None),
    ("Cloudflare", "Cloudflare MCP servers", "Tier 3",
     "FOUND (named + verifiable path)", "partners@cloudflare.com"),
    ("Neon", "Neon MCP Server", "Tier 3",
     "FOUND as a channel (first-party program: Neon Partner "
     "Program)", None),
]

# (name, org, role as printed in the prep, emails or None, note)
# Excluded by the prep's own flags, NOT ingested: Enes Akar
# (third-party directories only), Teo Gonzalez (aggregator-sourced,
# "not relied on"), Luke Mulks (social-profile sourcing only).
P6_PERSONS = [
    ("Simina Pasat", "GitHub", "VP Product at GitHub", None,
     "Named in the MCP Registry launch-partner press release; the "
     "working route is the partnerships mailbox."),
    ("Debbie O'Brien", "Microsoft",
     "Principal Program Manager at Microsoft (Playwright MCP lead)",
     None, "Named in podcast sources retrieved by the prep; the "
     "route runs through the project channels."),
    ("Eric Ciarla", "Firecrawl", "co-founder", None,
     "Named as a company contact in Firecrawl's Series B release; "
     "the working route is the partnerships mailbox."),
    ("Eduard Ruzga", "Desktop Commander",
     "solo maintainer/creator of Desktop Commander", None,
     "Signs his own comments in the project's GitHub issues as "
     "'CEO, Desktop Commander'. No email ingested: the only "
     "circulating address is a third-party claim the prep flags "
     "NOT first-party verified."),
    ("Jack Adamson", "Anthropic, PBC",
     "maintainer of record, mcp-server-fetch",
     ["jadamson@anthropic.com"],
     "Named with this email in the project's own package metadata "
     "(src/fetch/pyproject.toml) - first-party project metadata."),
    ("Cody De Arkland", "Sentry", "Head of Developer Experience "
     "at Sentry", None,
     "Quoted in Sentry's own MCP Server Monitoring press release."),
    ("Ricky Robinett", "Cloudflare",
     "VP Developer Relations at Cloudflare", None,
     "Role corroborated by two independent third-party sources; "
     "the working route is the partners mailbox."),
    ("Nikita Shamgunov", "Neon", "founder of Neon", None,
     "Named in current coverage as a Databricks VP of Engineering "
     "following the acquisition; no direct contact path published."),
]

# (org, outcome class, detail, url or None) — SENDS record, 2026-11-12
P6_OUTCOMES = [
    ("GitHub", "EMAIL FAILED - NOT SENT",
     "Route partnerships@github.com; send attempted after owner "
     "approval and failed inside the mail service (HTTP 500). Send "
     "still owed when the mail service recovers.", None),
    ("Microsoft", "POSTED",
     "GitHub issue on microsoft/playwright-mcp, posted as "
     "cetech-ai. Follow-up due 2026-11-13 ~19:45 PT.",
     "https://github.com/microsoft/playwright-mcp/issues/1860"),
    ("Upstash", "EMAIL FAILED - NOT SENT",
     "Route context7@upstash.com; send attempted after owner "
     "approval and failed inside the mail service (HTTP 503). Send "
     "still owed when the mail service recovers.", None),
    ("Firecrawl", "EMAIL NOT ATTEMPTED - HELD",
     "Route partnerships@firecrawl.dev; held after the two-failure "
     "service pattern. Send owed when the connector recovers.",
     None),
    ("Desktop Commander", "POSTED",
     "GitHub issue on wonderwhy-er/DesktopCommanderMCP, "
     "maintainer-direct. Follow-up due 2026-11-13 ~19:45 PT.",
     "https://github.com/wonderwhy-er/DesktopCommanderMCP/"
     "issues/1057"),
    ("Anthropic, PBC", "POSTED",
     "GitHub issue on modelcontextprotocol/servers (mcp-server-"
     "fetch, alternate C). Follow-up due 2026-11-13 ~19:45 PT.",
     "https://github.com/modelcontextprotocol/servers/"
     "issues/5544"),
    ("Sentry", "EMAIL NOT ATTEMPTED - HELD",
     "Route partnership-platform@sentry.io; held after the "
     "two-failure service pattern. Send owed when the connector "
     "recovers.", None),
    ("Exa", "EMAIL NOT ATTEMPTED - HELD",
     "Route partnerships@exa.ai (mailbox only; the aggregator-"
     "sourced named individual was not relied on). Send owed when "
     "the connector recovers.", None),
    ("Brave", "EMAIL NOT ATTEMPTED - HELD",
     "Route bizdev@brave.com; held after the two-failure service "
     "pattern. Send owed when the connector recovers.", None),
    ("Supabase", "PREPARED, NOT SUBMITTED",
     "Partner Program web form (browser session required); a "
     "third-party-circulated address for this account was NOT "
     "used, per the prep-book flag, and is not recorded in the "
     "graph.", None),
    ("Cloudflare", "EMAIL NOT ATTEMPTED - HELD",
     "Route partners@cloudflare.com; held after the two-failure "
     "service pattern. Send owed when the connector recovers.",
     None),
    ("Neon", "PREPARED, NOT SUBMITTED",
     "Partner Program web form (browser session required).", None),
]


def extract_slice3() -> list:
    out = []
    for org, product, tier, status, route in P6_COMPANIES:
        fields = {"name": org, "product": product, "tier": tier,
                  "route_status": status,
                  "source_ref": "p6-outreach-prep-20261112.md"}
        if route:
            fields["contact_route"] = route
        out.append(payload(
            "company", f"company-p6-{slug(org, 40)}",
            "cyan-eclipse-p6-outreach", slug(org, 40), fields,
            {"p6_account": org}))
    for name, org, role, emails, note in P6_PERSONS:
        fields = {"name": name, "organization": org, "role": role,
                  "sourcing_note": note,
                  "source_ref": "p6-outreach-prep-20261112.md"}
        if emails:
            fields["emails"] = emails
        out.append(payload(
            "person", f"person-p6-{slug(name, 40)}",
            "cyan-eclipse-p6-outreach", slug(name, 40), fields,
            {"p6_contact": slug(name, 40)}))
    for org, outcome, detail, url in P6_OUTCOMES:
        fields = {"event": f"P-6 first touch - {org}: {outcome}",
                  "event_date": "2026-11-12",
                  "outcome": outcome, "detail": detail,
                  "company_ref": f"company-p6-{slug(org, 40)}",
                  "source_ref":
                      "p6-outreach-prep-20261112-SENDS.md"}
        if url:
            fields["url"] = url
        out.append(payload(
            "activity", f"activity-p6-{slug(org, 40)}",
            "cyan-eclipse-p6-outreach", slug(org, 40), fields,
            {"p6_outcome": slug(org, 40)}))
    return out


# ------------------------------------------------------------------
# Slice 4 — bids: register rows + packet POCs (S-5)
# ------------------------------------------------------------------

# Packet SEND-RECORDs whose To lines carry named POCs. Name/role
# map keyed by the printed address; every entry was read first-hand
# in its SEND-RECORD before being entered here.
SEND_RECORD_PACKETS = [
    "va-ocdao-ss-20261104", "scars-20261109",
    "ai-enabled-support-20261109", "ar-arc4-20261109",
    "pmx-281-20261109", "va-vbecs-ss-20261113",
    "navy-enterprise-it-ss-20261113",
    "fisma-authentication-20261109",
]
POC_BY_EMAIL = {
    "kevin.andujar@va.gov": ("Kevin Andujar", None,
                             "va-ocdao-ss-20261104"),
    "anne.torian@us.af.mil": ("Anne Torian", None, "scars-20261109"),
    "daniel.douglass.2@us.af.mil": ("Daniel Douglass", None,
                                    "scars-20261109"),
    "valerie.cuevas@us.af.mil": ("Valerie Cuevas", None,
                                 "ai-enabled-support-20261109"),
    "logan.wherley@us.af.mil": ("Logan Wherley", None,
                                "ai-enabled-support-20261109"),
    "lieselotte.h.pattee.civ@army.mil": ("Lieselotte H. Pattee",
                                         None, "ar-arc4-20261109"),
    "jennifer.d.davis1.civ@us.navy.mil": ("Jennifer Davis",
                                          "Contract Specialist",
                                          "pmx-281-20261109"),
    "raymond.c.cannon2.civ@us.navy.mil": ("Raymond Cannon",
                                          "Contracting Officer",
                                          "pmx-281-20261109"),
    "richard.melton2@va.gov": ("Richard Melton", "Contract "
                               "Specialist", "va-vbecs-ss-20261113"),
    "diana.armesto@va.gov": ("Diana Armesto", "Contracting Officer",
                             "va-vbecs-ss-20261113"),
    "john.c.lowney.civ@us.navy.mil": ("John Lowney", None,
                                      "navy-enterprise-it-ss-20261113"),
    "amy.m.miller50.civ@mail.mil": ("Amy M. Miller",
                                    "Contracting Officer",
                                    "ustranscom-sms-aoi-20261111"),
    "carrie.e.boeding.civ@mail.mil": ("Carrie Boeding",
                                      "Contract Specialist",
                                      "ustranscom-sms-aoi-20261111"),
    "daniel.r.griffith14.civ@mail.mil": ("Daniel Griffith",
                                         "Contract Specialist",
                                         "ustranscom-sms-aoi-20261111"),
}
# Named POCs printed in packet records without a personal address
# (functional route only). (name, role, packet, note)
POC_NO_EMAIL = [
    ("Constance Cobb", "primary point of contact (SAM record)",
     "fisma-authentication-20261109",
     "Named as primary POC in the SAM record quoted in the packet "
     "SEND-RECORD; the notice prescribes the single submission "
     "mailbox TCSCAcquisitionOps@treasury.gov, which is carried on "
     "the send activity, not on this record."),
    ("Todd R. Ashcroft", "secondary point of contact (SAM record)",
     "fisma-authentication-20261109",
     "Named as secondary POC in the SAM record quoted in the "
     "packet SEND-RECORD; route as for the primary POC."),
    ("Mikayla Sequeira", "point of contact (RFI)",
     "apex-rfi-20261029",
     "Named in the packet records as an APEX RFI POC; the us.af.mil "
     "address stands in the RFI PDF of record (section 3.1) and is "
     "not printed in the packet markdown, so no address is "
     "ingested."),
    ("Venezia Delgadillo", "point of contact (RFI)",
     "apex-rfi-20261029",
     "Named in the packet records as an APEX RFI POC; address "
     "posture as for the other APEX POC."),
    ("Ariah Graham", "Agreements Officer",
     "eicam-aoi-20261109",
     "Named in the packet SEND-RECORD's notice analysis (E-ICAM "
     "AOI under DISA CSO HC108425S0001); the route is the DITCO OTA "
     "Team mailbox, carried on the register deal record's source "
     "packet."),
    ("Brianna Morphis", "Agreements Specialist",
     "eicam-aoi-20261109",
     "Named in the packet SEND-RECORD's notice analysis; route as "
     "for the Agreements Officer."),
    ("Lisa Pendragon", "Agreements/Contracting Officer",
     "amc-a6-it-cso-20261112",
     "Named with address lisa.pendragon@us.af.mil in the leads "
     "DISPOSITIONS record (2026-11-11) as an AMC/A6 CSO submission "
     "route; the address is carried in the note of record there. "
     "Ingested as a named POC without an emails field because the "
     "packet itself has not been re-read for this slice."),
    ("Elijah Dodd", "Contract Specialist",
     "amc-a6-it-cso-20261112",
     "Named with address elijah.dodd@us.af.mil in the leads "
     "DISPOSITIONS record (2026-11-11); posture as for the other "
     "AMC/A6 POC."),
    ("Sherill Johnson", "Procurement Agent (FDOT)",
     "fdot-psee-rfi-20261106",
     "Named in the packet QUESTIONS-SENT as the RFI's designated "
     "contact ('Sherill Johnson, Procurement Agent'); the register "
     "receipt prints 'Sherill R. Johnson'. The route mailbox "
     "CO.Purch@dot.state.fl.us is functional and is carried on the "
     "send activity, not on this record."),
]

ROW_FIELDS = (
    ("pursuit", "name"), ("agency", "agency"),
    ("counterparty", "counterparty"), ("deadline_et", "deadline_et"),
    ("status", "status"), ("submitted", "submitted"),
    ("receipt", "receipt"), ("check_back", "check_back"),
    ("what_due", "what_due"), ("note", "note"),
)


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(
        r"\(.*?\)", "", name)).strip().lower()


def extract_slice4(register: Path, bids_dir: Path,
                   slice1_roster: list) -> tuple:
    reg = json.loads(register.read_text(encoding="utf-8"))
    out, collisions = [], []
    roster = {_norm_name(n): rid for rid, n in slice1_roster}
    agencies = {}
    # Waiting rows whose send is already carried by a packet
    # SEND-RECORD activity below (no double-count of one send).
    covered_send_rows = {"va-ocdao-wait", "pmx-281-wait",
                         "va-vbecs-ss-wait", "sms-tdp-poc-wait"}
    for cls in ("live", "waiting", "closed"):
        for row in reg.get(cls, []):
            fields = {"register_id": row["id"],
                      "register_class": cls}
            for src, dst in ROW_FIELDS:
                if row.get(src):
                    fields[dst] = row[src]
            if "name" not in fields:
                fields["name"] = row["id"]
            out.append(payload(
                "deal", f"deal-bid-{slug(row['id'], 44)}",
                "cyan-eclipse-contracts-register", row["id"],
                fields, {"register_id": row["id"]}))
            if row.get("agency"):
                agencies.setdefault(row["agency"], row["id"])
            if cls == "waiting" and row.get("submitted"):
                m = EVIDENCE_RE.search(row["submitted"])
                sent = re.search(r"\bSENT\b|\bSUBMITTED\b|"
                                 r"intro sent",
                                 row["submitted"])
                if sent and row["id"] not in covered_send_rows:
                    out.append(payload(
                        "activity",
                        f"activity-send-{slug(row['id'], 44)}",
                        "cyan-eclipse-contracts-register",
                        row["id"],
                        {"event": f"Submission recorded - "
                                  f"{row.get('pursuit', row['id'])}",
                         "evidence_class": (
                             m.group(0) if m else
                             "as recorded (the register row "
                             "carries no evidence-class token)"),
                         "detail": row["submitted"],
                         "deal_ref": f"deal-bid-"
                                     f"{slug(row['id'], 44)}",
                         "source_ref":
                             "contracts-deadline-watch register "
                             "(waiting row)"},
                        {"register_send": row["id"]}))
    for agency, rid in agencies.items():
        out.append(payload(
            "company", f"company-bid-{slug(agency, 40)}",
            "cyan-eclipse-contracts-register", rid,
            {"name": agency,
             "role": "agency / office (contracts register)"},
            {"register_agency": agency}))
    # Packet SEND-RECORDs: send activities + POC persons.
    persons = {}
    for packet in SEND_RECORD_PACKETS:
        sr = bids_dir / packet / "SEND-RECORD.md"
        if not sr.exists():
            continue
        text = sr.read_text(encoding="utf-8")
        m = EVIDENCE_RE.search(text)
        state = m.group(0) if m else "as recorded"
        to_line = ""
        for line in text.splitlines():
            if "To:" in line and "@" in line:
                to_line = line.strip().lstrip("-* ").strip("*")
                break
        fields = {"event": f"Send record - {packet}",
                  "evidence_class": state,
                  "source_ref": f"bids/{packet}/SEND-RECORD.md"}
        if to_line:
            fields["route_line"] = to_line
        out.append(payload(
            "activity", f"activity-send-{slug(packet, 44)}",
            "cyan-eclipse-bid-packet", packet, fields,
            {"packet_send": packet}))
        for email in EMAIL_RE.findall(text):
            e = email.lower().rstrip(".,;")
            if e in POC_BY_EMAIL:
                name, role, pk = POC_BY_EMAIL[e]
                persons.setdefault(name, (role, pk, e))
    sms = bids_dir / "ustranscom-sms-aoi-20261111" / \
        "SEND-RECORD-POC-MESSAGE.md"
    if sms.exists():
        text = sms.read_text(encoding="utf-8")
        out.append(payload(
            "activity", "activity-send-ustranscom-sms-poc-message",
            "cyan-eclipse-bid-packet", "ustranscom-sms-aoi-20261111",
            {"event": "POC contact message - USTRANSCOM SMS AoI 003 "
                      "(TDP alternative transmission request)",
             "evidence_class": "SENT-UNVERIFIED",
             "detail": "Sent 2026-11-12 05:35 PT to the three "
                       "Contracting POCs under owner tap; 24h NDR "
                       "watch (SEND-RECORD-POC-MESSAGE.md).",
             "source_ref": "bids/ustranscom-sms-aoi-20261111/"
                           "SEND-RECORD-POC-MESSAGE.md"},
            {"packet_send": "ustranscom-sms-poc"}))
        for email in EMAIL_RE.findall(text):
            e = email.lower().rstrip(".,;")
            if e in POC_BY_EMAIL:
                name, role, pk = POC_BY_EMAIL[e]
                persons.setdefault(name, (role, pk, e))
    # persons: name -> [role, packet, email or None, note or None]
    persons = {n: [r, pk, e, None] for n, (r, pk, e)
               in persons.items()}
    for name, role, packet, note in POC_NO_EMAIL:
        if name in persons:
            persons[name][3] = note
        else:
            persons[name] = [role, packet, None, note]
    for name, data in sorted(persons.items()):
        role, packet, email, note = data
        fields = {"name": name, "source_ref": f"bids/{packet}/"}
        if role:
            fields["role"] = role
        if email:
            fields["emails"] = [email]
        if note:
            fields["note"] = note
        fields["pursuit_ref"] = packet
        existing = roster.get(_norm_name(name))
        if existing:
            link_fields = {
                "event": f"Identity link - {name} already in the "
                         f"graph as {existing} (slice 1); the "
                         f"slice-4 source is linked, not duplicated",
                "existing_record_id": existing,
                "also_seen_as": (f"{role + ', ' if role else ''}"
                                 f"point of contact, {packet}"),
                "source_ref": f"bids/{packet}/"}
            if email:
                link_fields["emails"] = [email]
            out.append(payload(
                "activity",
                f"activity-link-{slug(name, 40)}",
                "cyan-eclipse-bid-packet", packet, link_fields,
                {"identity_link": existing}))
            collisions.append({"name": name,
                               "existing_record_id": existing,
                               "slice4_source": packet,
                               "disposition": "identity-link "
                                              "activity; no second "
                                              "person record"})
            continue
        out.append(payload(
            "person", f"person-bidpoc-{slug(name, 40)}",
            "cyan-eclipse-bid-packet", packet, fields,
            {"bid_poc": slug(name, 40)}))
    return out, collisions


# ------------------------------------------------------------------
# Scan (MUS-C1-005 procedure, parameterized) + CLI
# ------------------------------------------------------------------

PHONE_RE = re.compile(
    r"(?<!\d)(?:\+1[\s.\-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.\-])"
    r"\d{3}[\s.\-]\d{4}(?!\d)")
SCAN_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def do_scan(server_py: Path, state: Path) -> dict:
    guard_state_path(state)
    state_data = json.loads(state.read_text(encoding="utf-8"))
    ids = [rid for rid, r in state_data["records"].items()
           if r.get("status") != "merged_into"]
    session = Session(server_py, state)
    matches, scanned = [], 0
    try:
        for rid in ids:
            out, is_err = session.tool(
                "record_read",
                {"record_id": rid, "surface": "analytic"})
            if is_err:
                raise SystemExit(f"scan read failed: {rid}: {out}")
            for fname, value in out["fields"].items():
                vals = value if isinstance(value, list) else [value]
                for item in vals:
                    if isinstance(item, str):
                        scanned += 1
                        for pat, label in (
                                (SCAN_EMAIL_RE, "email"),
                                (PHONE_RE, "phone")):
                            for mfound in pat.findall(item):
                                matches.append((rid, fname, label,
                                                mfound))
        ledger, _ = session.tool("receipt_list", {"limit": 100000})
    finally:
        session.close()
    return {"records_read": len(ids), "string_values_scanned":
            scanned, "matches": matches,
            "chain_valid": ledger["chain_valid"],
            "receipt_count": len(ledger["receipts"]),
            "chain_head": ledger["receipts"][-1]["hash"]
            if ledger["receipts"] else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("extract2")
    p.add_argument("--leads", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("extract3")
    p.add_argument("--out", required=True)

    p = sub.add_parser("extract4")
    p.add_argument("--register", required=True)
    p.add_argument("--bids-dir", required=True)
    p.add_argument("--slice1-roster", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--collisions-out", required=True)

    p = sub.add_parser("ingest")
    p.add_argument("--server", required=True)
    p.add_argument("--state", required=True)
    p.add_argument("--slice", required=True)

    p = sub.add_parser("scan")
    p.add_argument("--server", required=True)
    p.add_argument("--state", required=True)

    args = ap.parse_args(argv)
    if args.cmd == "extract2":
        payloads = extract_slice2(Path(args.leads))
    elif args.cmd == "extract3":
        payloads = extract_slice3()
    elif args.cmd == "extract4":
        roster = json.loads(Path(args.slice1_roster).read_text(
            encoding="utf-8"))
        payloads, collisions = extract_slice4(
            Path(args.register), Path(args.bids_dir), roster)
        Path(args.collisions_out).write_text(
            json.dumps(collisions, indent=1), encoding="utf-8")
        print(json.dumps({"collisions": collisions}, indent=1))
    if args.cmd in ("extract2", "extract3", "extract4"):
        Path(args.out).write_text(
            json.dumps(payloads, indent=1, ensure_ascii=False),
            encoding="utf-8")
        counts = {}
        for pl in payloads:
            counts[pl["kind"]] = counts.get(pl["kind"], 0) + 1
        print(json.dumps({"extracted": len(payloads),
                          "counts": counts}))
    elif args.cmd == "ingest":
        from c1_loader import do_ingest
        print(json.dumps(do_ingest(Path(args.server),
                                   Path(args.state),
                                   Path(args.slice)), indent=1))
    elif args.cmd == "scan":
        print(json.dumps(do_scan(Path(args.server),
                                 Path(args.state)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
