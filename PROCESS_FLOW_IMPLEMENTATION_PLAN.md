# GeM-Sentinel — Process Flow Re-alignment Plan (2026-09-13)

Functional foundation: user-provided tender process specification (MoPNG / DOE procurement
manual based). Product boundary: **AI-assisted bid compliance & evidence verification —
not an autonomous tender-awarding authority.**

## Preserved (core, untouched mechanics)
- Rule engine (deterministic) + AI explanation ("The Rule Engine Decides. AI Only Explains.")
- Consent management (no fetch before consent), vault, Verify-Once-Apply-Many
- Bidder portal (discovery, eligibility gate, application, clarifications, grievance)
- Officer/L2/Admin portals, jurisdiction routing, SLA
- Override Accountability Engine (re-auth, justification, admin cosign, vigilance)
- Hash-chained tamper-evident audit trail
- Auth: JWT + gs_access cookie + X-GS-Token + gs_token query (proxy-stripping proof)

## Implemented in this round

### P1 — Data model & backend
1. `ProcuringOrganization` model: name, short_code, org_type
   (MINISTRY / DEPARTMENT / CPSE / DIRECTORATE / REFINEY / REGULATORY),
   procurement_division, committee_policy (min members, finance+user reps),
   authority_bands JSON: [{min_value, max_value, review_role_title, approval_role_title,
   committee_required}] — per-org, value-driven (no hard-coded L1/L2/L3 labels).
2. `Tender` + `organization_id` FK (legacy `organization` string kept in sync),
   `procurement_method` (GEM_BID / CUSTOM_BID / TWO_PACKET_BID / OPEN_TENDER / …).
3. `TenderCorrigendum` model: tender_id, seq_no, title, published_at, summary,
   changes JSON, deadline_extended_to, published_by.
4. `TenderRule` + `compliance_layer` (STATUTORY / SECTOR_TECHNICAL),
   `clause_ref` (e.g. "Spec §4.2"), existing fields unchanged.
5. Endpoints:
   - Admin: GET/POST `/admin/organizations`, GET/POST `/admin/tenders/{id}/corrigenda`
     (publish → tender.rules_version += 1, report_stale=True on active apps,
     notifications to assigned officers + applicants, audit entry).
   - Bidder & Officer tender detail: + `procuring_organization` (with resolved
     authority chain for this tender's value), + `corrigenda`, rules grouped by
     `compliance_layer` with `clause_ref`.
   - Verification report: + `rules_version_at_run`, + `stale_due_to_corrigendum`,
     + `system_boundary` note, `recommendation` re-framed as review recommendation.
   - Application: `snapshot_frozen_at` set at submit; documents frozen
     (clarification responses appended with purpose=CLARIFICATION_RESPONSE only).

### P2 — Seed (petroleum sector reality)
6. 11 procuring organizations: MoPNG Secretariat, DGH, PNGRB, ONGC, IOC, BPCL,
   HPCL, GAIL (India), Oil India, EIL, CPCL — each with procurement division +
   authority bands (e.g. CPCL: ≤₹25L Tender Evaluation Officer → ≤₹2Cr Tender
   Committee → above Competent Financial Authority).
7. Tenders reassigned across organizations + procurement methods:
   - TDR-2026-001 Lubricants (Belagavi) → CPCL, GEM_BID
   - TDR-2026-002 PPE (Hubli) → IOC, GEM_BID
   - TDR-2026-003 IT Modernization → MoPNG Secretariat, CUSTOM_BID
   - TDR-2026-004 Stationery (closed) → Oil India, GEM_BID
8. Sector-technical rules (SECTOR_TECHNICAL layer) with clause refs:
   - Lubricants tender: BIS IS 1141 cert, API/industrial standards, OEM
     authorization, refinery supply experience, PESO licence (hazardous), factory
     licence.
   - PPE tender: BIS/ISI certification, OEM authorization, factory licence.
   - Statutory layer (GST, UDYAM, PAN, EPFO, ESIC, MCA21, STARTUP, NSIC,
     BLACKLIST, PROFILE) unchanged.
9. Corrigendum #1 on TDR-2026-001 (BIS standard revision + deadline extension)
   to demonstrate corrigendum-aware verification (existing t1 reports marked
   stale).

### P3 — Frontend
10. Bidder tender detail: procuring organization card (org, type, division,
    resolved award-authority chain), corrigenda panel, two-layer grouped
    eligibility checklist (Layer 1 statutory / Layer 2 sector technical),
    "bid-time snapshot frozen" indicator on application.
11. Officer review: ReportTab → clause-by-clause matrix (clause ref | layer |
    rule | source | status | evidence | note) grouped by compliance layer;
    stale banner when a corrigendum post-dates the last run; decision panel
    boundary statement ("System recommends review status — award rests with
    Tender Committee / Competent Authority").
12. Admin: tender detail + "Corrigenda" section (list + publish form);
    tender create/edit + organization & procurement method; organizations page
    (list + authority bands).
13. i18n en + hi for all new strings.

### P4 — Verification
14. Critical test suite re-run (23 tests); E2E: bidder sees org + corrigenda +
    two-layer checklist; admin publishes corrigendum #2 → officer sees stale
    banner → re-run verification → fresh matrix.

## Explicitly OUT of scope (product boundary)
- Declaring L1 / awarding tender / financial bid opening / reverse auction /
  contract placement / CRAC / payment (Phase C.6–C.10, D) — decisions remain
  with Tender Committee & Competent Authority per the specification.
