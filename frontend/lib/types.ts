// V2.1 role model - explicit titles. L1/L2 are financial RANKS (lowest /
// second-lowest acceptable evaluated bids), never officer role names.
export type Role =
  | "BIDDER"
  | "BID_PREPARER"
  | "SIGNATORY"
  | "INDENTOR"
  | "PROCUREMENT_OFFICER"
  | "BID_OPENING_OFFICER"
  | "TECHNICAL_EVALUATOR"
  | "FINANCE_EVALUATOR"
  | "COMMITTEE_CHAIR"
  | "COMMITTEE_MEMBER"
  | "CFA"
  | "CONTRACT_MANAGER"
  | "CONTRACT_OFFICER"
  | "INSPECTION_OFFICER"
  | "PAYMENT_OFFICER"
  | "ADMIN"
  | "AUDITOR"
  | "L1_OFFICER" // legacy (migration bridge)
  | "L2_OFFICER"; // legacy (migration bridge)

export interface User {
  id: string;
  email: string;
  full_name: string;
  mobile?: string | null;
  role: Role;
  status: string;
  is_verified?: boolean;
  profile?: BidderProfile;
}

export interface BidderProfile {
  company_name: string;
  trade_name?: string | null;
  pan_masked?: string | null;
  gstin_masked?: string | null;
  udyam_masked?: string | null;
  business_type?: string | null;
  business_category?: string | null;
  registered_address?: string | null;
  city?: string | null;
  district?: string | null;
  state?: string | null;
  pincode?: string | null;
  authorized_rep_name?: string | null;
  authorized_rep_email?: string | null;
  authorized_rep_phone?: string | null;
  annual_turnover?: number | null;
  established_year?: number | null;
  registration_date?: string | null;
  filing_status?: string | null;
  local_content_percent?: number | null;
  profile_completion: number;
}

export interface TenderCard {
  id: string;
  tender_no: string;
  title: string;
  department: string;
  organization: string;
  district: string;
  state: string;
  category: string | null;
  product_service_category?: string | null;
  value_min: number | null;
  value_max: number | null;
  close_date: string;
  msme_preference: boolean;
  make_in_india_required: boolean;
  startup_eligible: boolean;
  eligibility_badge:
    | "ELIGIBLE" | "CONDITIONAL" | "NOT_ELIGIBLE"
    | "BLACKLISTED" | "MISSING_DOCUMENTS"
    | "NOT_CHECKED" | "UNABLE_TO_VERIFY";
  apply_allowed?: boolean;
  apply_blocked_reasons?: string[];
  bookmarked: boolean;
  days_left: number;
}

export interface RuleCheck {
  rule_key: string;
  label: string;
  source: string;
  mandatory: boolean;
  result: "PASS" | "FAIL" | "UNABLE_TO_VERIFY" | "N/A";
  message: string;
  hard_disqualification?: boolean;
  compliance_layer?: "STATUTORY" | "SECTOR_TECHNICAL";
  clause_ref?: string | null;
}

export interface ProcuringOrg {
  id: string;
  name: string;
  short_code: string;
  org_type: string;
  parent_ministry?: string | null;
  procurement_division?: string | null;
  committee_min_members?: number;
  committee_requires_finance?: boolean;
  committee_requires_user_rep?: boolean;
}

export interface AuthorityBand {
  band: string;
  review_role_title: string;
  approval_role_title: string;
  committee_required: boolean;
}

export interface Corrigendum {
  seq_no: number;
  title: string;
  summary: string;
  changes?: Array<{ clause: string; old: string; new: string; description: string }>;
  published_at: string | null;
  deadline_extended_to?: string | null;
}

export interface SourceResult {
  status: string;
  message?: string;
  retrieved_at?: string | null;
}

export interface RuleResult {
  rule_id: string;
  rule_key: string;
  label: string;
  source: string;
  mandatory: boolean;
  result: "PASS" | "FAIL" | "UNABLE_TO_VERIFY" | "N/A";
  weight: number;
  hard_disqualification: boolean;
  message: string;
  evidence?: Record<string, unknown> | null;
  compliance_layer?: "STATUTORY" | "SECTOR_TECHNICAL";
  clause_ref?: string | null;
  /** Evidence-strength ladder (spec §13): S0 none → S4 cross-verified. */
  evidence_strength?: string;
  min_evidence_strength?: string;
}

export interface CrossCheck {
  field: string;
  bidder_value: string | null;
  source_value: string | null;
  source: string;
  result: "MATCH" | "MISMATCH" | "UNABLE_TO_VERIFY";
}

export interface AuthenticityFinding {
  document_id: string;
  document_name: string;
  doc_type: string;
  status: "VERIFIED" | "SUSPICIOUS" | "UNABLE_TO_VERIFY" | "MANUAL_REVIEW_REQUIRED";
  reasons: string[];
  sha256?: string | null;
  seal?: { bbox: number[]; circularity: number; area: number } | null;
  signature?: { bbox: number[]; area: number; components?: number } | null;
  seal_similarity_to_trusted?: number | null;
  signature_similarity_to_trusted?: number | null;
  tampering_indicators?: string[];
  field_consistency?: { field: string; document_value: string; source_value: string; result: string }[];
  digital_signature?: { present: boolean | null; note?: string } | null;
  pdf?: Record<string, unknown> | null;
  duplicate_of?: string | null;
  confidence?: number;
}

export interface XaiEvidence {
  source?: string;
  message?: string;
  status?: string;
  detail?: Record<string, unknown> | null;
}

export interface XaiBasis {
  rule: string | null;
  result: string;
  weight: number | null;
  mandatory: boolean;
  impact: string;
  clause_ref: string | null;
  statutory_basis: string | null;
  cited_passage_id: string | null;
  evidence: XaiEvidence | null;
}

export interface XaiCitation {
  id: string;
  title: string;
  refs: string[];
}

export interface XaiRetrievedPassage {
  id: string;
  title: string;
  refs: string[];
  score: number;
  matched_on: string[];
}

export interface ExplanationDetail {
  version: string;
  language: string;
  executive_summary: { headline: string[]; counts: Record<string, number> };
  basis_of_decision: XaiBasis[];
  cross_verification: { field: string; bidder_value: string | null; source_value: string | null; source: string }[];
  document_findings: { document: string; finding: string; detail: string }[];
  evidence_trail: { source: string; status: string; message: string | null; retrieved_at: string | null; cached: boolean }[];
  remediation: { action: string; reason?: string }[];
  citations: XaiCitation[];
  governance: {
    decided_by: string;
    ai_role: string;
    retrieval: string;
    final_authority: string;
    retrieved_passages?: XaiRetrievedPassage[];
  };
}

export interface VerificationReport {
  id: string;
  run_no: number;
  generated_at: string;
  compliance_score: number;
  risk_level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  risk_label?: string;
  eligibility_result: "ELIGIBLE" | "CONDITIONAL" | "NOT_ELIGIBLE";
  rule_results: RuleResult[];
  cross_verification: CrossCheck[];
  mismatches: { field: string; bidder_value: string; source_value: string; source: string }[];
  missing_documents: string[];
  expired_documents: { document_id: string; name: string; expiry_date: string }[];
  blacklist_findings: Record<string, unknown>[];
  low_confidence_fields: { document_id: string; name: string; confidence: number }[];
  source_results: Record<string, SourceResult>;
  authenticity: AuthenticityFinding[];
  ai_explanation: string | null;
  officer_summary?: OfficerSummary | null;
  ai_explanation_language?: string;
  explanation_detail?: ExplanationDetail | null;
  model_identifier: string | null;
  recommendation: string | null;
  triggered_by?: string | null;
  /** Share of applicable rules backed by authoritative evidence (spec §13). */
  coverage_pct?: number | null;
  strength_distribution?: Record<string, number>;
  determinism_hash?: string | null;
  report_version?: string | null;
  created_from_report_id?: string | null;
  rules_version_at_run?: number | null;
  tender_rules_version?: number;
  stale_due_to_corrigendum?: boolean;
  latest_corrigendum?: { seq_no: number; title: string; published_at: string | null } | null;
  system_boundary?: string;
}

export interface Clarification {
  id: string;
  reason: string;
  documents_needed?: string[];
  deadline?: string | null;
  status: "OPEN" | "RESPONDED" | "CLOSED" | "EXPIRED";
  response_text?: string | null;
  responded_at?: string | null;
  created_at: string;
}

export interface ApplicationSummary {
  id: string;
  application_no: string;
  company?: string;
  tender_no: string;
  tender_title?: string;
  district?: string;
  status: string;
  risk_level: string | null;
  compliance_score: number | null;
  eligibility_result?: string | null;
  fraud_flagged?: boolean;
  report_stale?: boolean;
  sla_due_at?: string | null;
  sla_breached?: boolean;
  submitted_at?: string | null;
  sealed?: boolean;
  bid_deadline_utc?: string | null;
  l1_recommendation?: {
    action: string;
    remarks: string | null;
    is_overridden: boolean;
    officer: string | null;
    at: string;
  } | null;
  override_history?: {
    id: string;
    severity: string;
    reason_category: string;
    status: string;
    admin_cosign_required: boolean;
    admin_cosigned_at?: string | null;
    vigilance_referral_id?: string | null;
  }[];
}

export interface OverrideChallenge {
  override_required: true;
  challenge: string;
  severity: "MINOR" | "MEDIUM" | "HIGH" | "CRITICAL" | "FRAUD";
  finding: {
    risk_level: string;
    eligibility_result: string;
    blacklisted: boolean;
    blacklist_findings: Record<string, unknown>[];
    failed_rules: { label: string; message: string; evidence?: Record<string, unknown> }[];
    unverified_mandatory: { label: string; message: string }[];
    mismatches: { field: string; bidder_value: string; source_value: string; source: string }[];
    compliance_score: number | null;
    ai_explanation?: string | null;
    report_generated_at?: string | null;
  };
  reason_categories: { id: string; label: string }[];
  min_justification_length: number;
  min_other_justification_length: number;
  requires_password_reauthentication: boolean;
  policy: string;
}

export interface DocumentItem {
  id: string;
  name: string;
  doc_type: string;
  doc_type_label?: string;
  source: string;
  source_classification: "GOVERNMENT_SOURCE" | "BIDDER_UPLOADED";
  is_government_source?: boolean;
  verification_status: string;
  issue_date?: string | null;
  expiry_date?: string | null;
  expired?: boolean;
  last_synced_at?: string | null;
  reference_id?: string | null;
  document_hash?: string | null;
  masked_identifier?: string | null;
  ocr_confidence?: number | null;
  authenticity_status?: string | null;
  version: number;
  file_available?: boolean;
  notes?: string | null;
}

export interface AppNotification {
  id: string;
  event_type: string;
  title: string;
  message: string;
  entity_type?: string | null;
  entity_id?: string | null;
  is_read: boolean;
  created_at: string;
}

// ---------------------------------------------------------------------------
// 11-stage procurement pipeline
// ---------------------------------------------------------------------------
export interface PipelineStageState {
  key: string;
  label: string;
  owner_role: string;
  owner_title: string;
  owner_name: string | null;
  owner_email: string | null;
  status: "PENDING" | "COMPLETED" | "TERMINATED";
  current: boolean;
  acted_by: string | null;
  acted_at: string | null;
  payload: Record<string, any> | null;
  status_label: string;
  certificate_no: string | null;
  actions: string[];
}

export interface PipelineState {
  application_no: string;
  tender_no: string;
  tender_title: string;
  status: string;
  my_role: string;
  my_stage: string | null;
  can_act_on_current: boolean;
  doc_scope: DocScope;
  certificates: Array<{ stage: string; label: string; no: string; url: string }>;
  stages: PipelineStageState[];
  current_stage: string | null;
}

export interface DocScope {
  role: string | null;
  scope_label: string;
  visible_types: string[];
  hidden_types: string[];
  visible_count: number;
  hidden_count: number;
}

export interface OfficerSummaryItem {
  check: string;
  status: string;
  finding: string | null;
  clause: string | null;
  action: string | null;
}

export interface OfficerSummary {
  verdict: string;
  score: number | null;
  risk: string | null;
  one_line: string;
  must_fix: OfficerSummaryItem[];
  watch_items: OfficerSummaryItem[];
  documents: { verified: number; flagged: string[]; missing: string[] };
  recommended_action: string | null;
  checks: { passed: number; failed: number; unverified: number; total: number };
}

// ---------------- auctions ----------------
export interface MyAuctionEntry {
  auction_id: string;
  auction_no: string;
  tender_no: string;
  tender_title: string;
  auction_type: "REVERSE" | "FORWARD";
  status: string;
  your_status: string;
  qualification_reason: string | null;
  initial_price: string | null;
  current_price: string | null;
  effective_end_at: string | null;
}

export interface AuctionParticipant {
  anonymous_id: string;
  ra_eligible: boolean;
  qualification_reason: string | null;
  suspended: boolean;
  initial_price: string | null;
  company?: string | null;
}

export interface AuctionBidEntry {
  sequence_number: number;
  anonymous_id: string | null;
  amount: string;
  server_received_at: string | null;
}

export interface AuctionResultEntry {
  rank: number | string;
  anonymous_id: string;
  bidder_user_id?: string;
  amount: string;
  price_source: string;
}

export interface AuctionResult {
  status: string;
  tie: boolean;
  initial_lowest: string | null;
  final_lowest: string | null;
  savings_amount: string | null;
  savings_pct: string | null;
  accepted_bid_count: number;
  ranking: AuctionResultEntry[];
}

export interface AuctionSnapshot {
  auction_id: string;
  auction_no: string;
  tender_id: string;
  auction_type: "REVERSE" | "FORWARD";
  title: string;
  status: string;
  server_time: string;
  scheduled_start_at: string | null;
  scheduled_end_at: string | null;
  effective_end_at: string | null;
  started_at: string | null;
  closed_at: string | null;
  seconds_remaining: number | null;
  min_increment: string;
  opening_reference_price: string | null;
  extension_count: number;
  auto_extend_enabled: boolean;
  leading: { amount: string; anonymous_id: string; sequence_number: number | null; server_received_at: string | null } | null;
  participants: AuctionParticipant[];
  participant_count: number;
  recent_bids: AuctionBidEntry[];
  result: AuctionResult | null;
  your_participation?: {
    anonymous_id: string;
    ra_eligible: boolean;
    suspended: boolean;
    qualification_reason: string | null;
    initial_price: string | null;
    current_price: string | null;
    bids: { sequence_number: number; amount: string; server_received_at: string | null }[];
  } | null;
}

export interface OfficerAuction {
  id: string;
  auction_no: string;
  tender_no: string;
  auction_type: string;
  title: string;
  status: string;
  scheduled_start_at: string | null;
  effective_end_at: string | null;
  eligible_participants: number;
}

export interface OfficerTender {
  id: string;
  tender_no: string;
  title: string;
  district: string;
  status: string;
  procurement_mode: string;
  procurement_mode_label: string;
  financial_bid_required: boolean;
  bids_open: boolean;
  effective_bid_deadline: string | null;
}

export interface AuctionIntegrity {
  auction_id: string;
  auction_no: string;
  auction_type: string;
  status: string;
  bid_chain: { valid: boolean; bids_checked: number; broken_at_sequence?: number };
  audit: { auction_events: number; audit_chain_valid: boolean };
  result_hash: string | null;
  bid_source_ips: { anonymous_id: string; distinct_ips: number; ips: string[] }[];
  shared_bid_ips: { masked_ip: string; participants: string[] }[];
  collusion_signals: {
    bidders_analyzed: number;
    risk_level: string;
    cartel_detected: boolean;
    indicators: { bidder_a: string; bidder_b: string; signal: string; weight: number; evidence: string }[];
    note: string;
  };
}
