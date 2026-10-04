"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Download, Eye, Lock, Play, Search } from "lucide-react";
import { api, downloadFile } from "@/lib/api";
import type { VerificationReport } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge, riskVariant, authenticityVariant } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/fields";
import { Tabs } from "@/components/ui/tabs";
import { Loading, AppStatusBadge, RiskBadge, EligibilityBadge, ResultBadge } from "@/components/app/widgets";
import { ReportTab } from "@/components/app/report-tab";
import { DocViewer, type DocViewerTarget } from "@/components/app/doc-viewer";
import { fmtDate, fmtDateTime, formatINR } from "@/lib/utils";

interface Review {
  application: any;
  bidder: any;
  tender: any;
  documents: any[];
  application_documents: any[];
  doc_scope?: { role: string | null; scope_label: string; visible_count: number; hidden_count: number } | null;
  pipeline?: { stages: any[]; current_stage: string | null } | null;
  my_role?: string | null;
  comparison_table: any[];
  verification_report: VerificationReport | null;
  last_verification: string | null;
  clarifications: any[];
  decisions: any[];
  overrides: any[];
  previous_applications: any[];
  timeline: any[];
  audit_trail: any[];
  report_stale: boolean;
}

const ROLE_LABELS: Record<string, string> = {
  BID_OPENING_OFFICER: "Bid Opening Officer",
  PROCUREMENT_OFFICER: "Bid Compliance Officer (AI Screening)",
  TECHNICAL_EVALUATOR: "Technical Evaluation Officer",
  FINANCE_EVALUATOR: "Financial Evaluation Officer",
  CFA: "Tender Approving Authority (CFA)",
  CONTRACT_MANAGER: "Contract Manager",
  CONTRACT_OFFICER: "Contract Execution Officer",
  INSPECTION_OFFICER: "Delivery & Inspection Officer",
  PAYMENT_OFFICER: "Payment & Settlement Officer",
  AUDITOR: "Audit / Compliance Officer",
  ADMIN: "System Admin",
  INDENTOR: "Indentor",
  COMMITTEE_CHAIR: "Committee Chair",
  COMMITTEE_MEMBER: "Committee Member",
};

const TABS = [
  { id: "pipeline", labelKey: "officer.pipeline" },
  { id: "report", labelKey: "officer.reportTitle" },
  { id: "overview", labelKey: "officer.bidderProfile" },
  { id: "docs", labelKey: "common.documents" },
  { id: "clar", labelKey: "bidder.clarificationRequests" },
  { id: "decisions", labelKey: "common.audit" },
  { id: "audit", labelKey: "common.audit" },
];

function OfficerSummaryCard({ s }: { s: any }) {
  const t = useTranslations();
  const v = s.verdict ?? "";
  const vColor = v.includes("DEBARRED")
    ? "bg-purple-100 text-purple-800 border-purple-200"
    : v === "NON-COMPLIANT"
      ? "bg-critical-100 text-critical-800 border-critical-200"
      : v === "MANUAL REVIEW REQUIRED"
        ? "bg-warning-100 text-warning-800 border-warning-200"
        : "bg-success-100 text-success-800 border-success-200";
  return (
    <Card className="mb-4 border-navy-200">
      <CardHeader>
        <CardTitle>{t("officer.officerSummary")}</CardTitle>
        <CardDescription>{t("officer.officerSummaryHint")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className={`rounded-md border px-3 py-1.5 text-sm font-bold ${vColor}`}>{v}</span>
          <span className="text-sm font-medium text-slate-800">{s.one_line}</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-3 text-xs">
          <div className="rounded-md bg-slate-50 p-2.5">
            <div className="font-semibold text-slate-700">{t("officer.summaryChecks")}</div>
            <div className="mt-0.5">{s.checks.passed} ✓ passed · {s.checks.failed} ✗ failed · {s.checks.unverified} ? unverified (of {s.checks.total})</div>
          </div>
          <div className="rounded-md bg-slate-50 p-2.5">
            <div className="font-semibold text-slate-700">{t("officer.summaryDocs")}</div>
            <div className="mt-0.5">{s.documents.verified} verified · {(s.documents.flagged ?? []).length} flagged · {(s.documents.missing ?? []).length} missing</div>
          </div>
          <div className="rounded-md bg-slate-50 p-2.5">
            <div className="font-semibold text-slate-700">{t("officer.summaryAction")}</div>
            <div className="mt-0.5">{s.recommended_action ?? "-"}</div>
          </div>
        </div>
        {s.must_fix.length > 0 && (
          <div className="rounded-md border border-critical-200 bg-critical-50 p-3">
            <div className="text-xs font-bold uppercase tracking-wide text-critical-800">{t("officer.summaryMustFix")}</div>
            <ul className="mt-1.5 space-y-1.5">
              {s.must_fix.map((f: any, i: number) => (
                <li key={i} className="text-sm text-critical-900">
                  <b>{f.check}</b>
                  {f.clause ? <span className="text-critical-700"> [{f.clause}]</span> : null} - {f.finding}
                  {f.action && <div className="text-xs text-critical-800"><b>Fix:</b> {f.action}</div>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {s.watch_items.length > 0 && (
          <div className="rounded-md border border-warning-200 bg-warning-50 p-3">
            <div className="text-xs font-bold uppercase tracking-wide text-warning-800">{t("officer.summaryWatch")}</div>
            <ul className="mt-1.5 space-y-1">
              {s.watch_items.map((w: any, i: number) => (
                <li key={i} className="text-sm text-warning-900">
                  <b>{w.check}</b>{w.clause ? <span> [{w.clause}]</span> : null} - {w.finding}
                  {w.action && <div className="text-xs"><b>Check:</b> {w.action}</div>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function OfficerReviewPage() {
  const t = useTranslations();
  const params = useParams<{ id: string }>();
  const [d, setD] = useState<Review | null>(null);
  const [err, setErr] = useState("");
  const [sealed, setSealed] = useState<{
    code: string;
    message: string;
    bid_deadline_utc: string | null;
    bid_deadline_ist: string | null;
    tender_no: string;
  } | null>(null);
  const [tab, setTab] = useState("pipeline");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [running, setRunning] = useState(false);
  const [decisionMsg, setDecisionMsg] = useState("");
  const [decOpen, setDecOpen] = useState<null | "clarification">(null);
  const [decReason, setDecReason] = useState("");
  const [decDocs, setDecDocs] = useState("");
  const [decDays, setDecDays] = useState("3");
  const [viewing, setViewing] = useState<DocViewerTarget | null>(null);

  const load = useCallback(() => {
    api<Review>(`/officer/applications/${params.id}`)
      .then((res) => {
        setSealed(null);
        setD(res);
      })
      .catch((e) => {
        const det = e?.detail;
        if (e?.status === 403 && det && typeof det === "object" && (det as any).code === "BIDS_SEALED") {
          setSealed(det as any);
          setD(null);
        } else {
          setErr(typeof det === "string" ? det : det?.message ?? JSON.stringify(det) ?? String(e));
        }
      });
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  if (sealed) {
    return (
      <div className="mx-auto max-w-2xl">
        <Card className="border-navy-200">
          <CardContent className="flex flex-col items-center gap-3 p-8 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-navy-50 text-navy-700">
              <Lock size={26} />
            </div>
            <h1 className="text-lg font-bold text-navy-900">{t("officer.sealedTitle")}</h1>
            <p className="text-sm text-slate-600">{sealed.message}</p>
            <div className="mt-1 w-full rounded-md border border-navy-100 bg-navy-50/50 p-3 text-sm text-slate-700">
              <div className="flex items-center justify-between">
                <span>{t("officer.sealedTender")}</span>
                <span className="font-mono font-medium">{sealed.tender_no}</span>
              </div>
              <div className="mt-1 flex items-center justify-between">
                <span>{t("officer.sealedOpens")}</span>
                <span className="font-medium text-navy-800">
                  {fmtDateTime(sealed.bid_deadline_ist)} (IST)
                </span>
              </div>
            </div>
            <p className="text-xs text-slate-500">{t("officer.sealedNote")}</p>
            <Link href="/officer/applications" className="mt-1 text-sm text-navy-700 hover:underline">
              ← {t("nav.queue")}
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (err) return <Alert variant="danger">{err}</Alert>;
  if (!d) return <Loading />;

  const app = d.application;
  const rep = d.verification_report;
  const finalStatuses = ["Approved", "Rejected", "Withdrawn", "Closed"];
  const canAct = !finalStatuses.includes(app.status);

  const runVerification = async () => {
    setRunning(true);
    setMsg(null);
    try {
      await api(`/officer/applications/${params.id}/run-verification`, { method: "POST" });
      setMsg({ ok: true, text: "AI verification completed. Report updated." });
      load();
    } catch (e2: any) {
      setMsg({ ok: false, text: e2?.detail ?? String(e2) });
    } finally {
      setRunning(false);
    }
  };

  const decide = async (action: string) => {
    setMsg(null);
    if (action === "REQUEST_CLARIFICATION") {
      if (!decReason.trim()) {
        setMsg({ ok: false, text: "Clarification reason is required." });
        return;
      }
    }
    if (action === "FLAG_SUSPECTED_FRAUD") {
      if (!window.confirm("Flag this application as suspected fraud? This notifies Admin and Vigilance monitoring.")) return;
    }
    try {
      const r = await api(`/officer/applications/${params.id}/decision`, {
        method: "POST",
        body: JSON.stringify({
          action,
          remarks: action === "REQUEST_CLARIFICATION" ? undefined : undefined,
          clarification_reason: action === "REQUEST_CLARIFICATION" ? decReason : undefined,
          clarification_documents: action === "REQUEST_CLARIFICATION" ? decDocs.split(",").map((x) => x.trim()).filter(Boolean) : [],
          clarification_deadline_days: Number(decDays) || 3,
        }),
      });
      setMsg({ ok: true, text: `Action recorded: ${action}` });
      setDecOpen(null);
      setDecReason("");
      setDecDocs("");
      load();
    } catch (e2: any) {
      const det = e2?.detail;
      setMsg({ ok: false, text: typeof det === "string" ? det : det?.message ?? JSON.stringify(det) ?? String(e2) });
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/officer/applications" className="text-sm text-navy-700 hover:underline">← {t("nav.queue")}</Link>
          <h1 className="mt-1 text-xl font-bold text-navy-900">{app.application_no}</h1>
          <p className="text-sm text-slate-500">
            {app.company} · {d.tender.tender_no} ({d.tender.title}) · {d.tender.district}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <AppStatusBadge status={app.status} />
            <RiskBadge risk={app.risk_level} />
            <EligibilityBadge value={app.eligibility_result ?? ""} />
            {d.my_role && <Badge variant="info">{t("officer.yourRole")}: {ROLE_LABELS[d.my_role] ?? d.my_role}</Badge>}
            {d.doc_scope && d.doc_scope.hidden_count > 0 && (
              <Badge variant="default">{t("officer.docScopeBanner", { count: d.doc_scope.hidden_count })}</Badge>
            )}
            {app.fraud_flagged && <Badge variant="danger">FRAUD FLAGGED</Badge>}
            {d.report_stale && <Badge variant="warning">{t("officer.reportStaleBadge")}</Badge>}
            {app.sla_breached && <Badge variant="critical">SLA breached (due {app.sla_due_at ? fmtDateTime(app.sla_due_at) : "-"})</Badge>}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Button variant="secondary" onClick={runVerification} loading={running} disabled={!canAct}>
            <Play size={15} /> {t("officer.runVerification")}
          </Button>
          <span className="text-xs text-slate-500">
            {t("officer.lastRun")}: {d.last_verification ? fmtDateTime(d.last_verification) : t("common.never")}
          </span>
        </div>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}
      <Alert variant="info">{t("officer.rerunHint")}</Alert>

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Tabs
            tabs={TABS.map((x) => ({
              id: x.id,
              label: t(x.labelKey),
              badge: x.id === "clar" ? d.clarifications.filter((c) => c.status === "OPEN").length : undefined,
            }))}
            active={tab}
            onChange={setTab}
          />

          {tab === "pipeline" && (
            <Card>
              <CardHeader>
                <CardTitle>{t("officer.processFlow")}</CardTitle>
                <CardDescription>{t("officer.processFlowHint")}</CardDescription>
              </CardHeader>
              <CardContent>
                <ol className="relative ml-2 space-y-1 border-l border-slate-200 pl-6">
                  {(d.pipeline?.stages ?? []).map((s: any, i: number) => (
                    <li key={s.key} className="relative pb-3">
                      <span className="absolute -left-[34px] top-0.5">
                        {s.status === "COMPLETED" ? (
                          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-success-100 text-success-700 text-xs">✓</span>
                        ) : s.status === "TERMINATED" ? (
                          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-critical-100 text-critical-700 text-xs">✕</span>
                        ) : s.current ? (
                          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-navy-700 text-white text-[10px]">▶</span>
                        ) : (
                          <span className="flex h-6 w-6 items-center justify-center rounded-full border-2 border-slate-200 bg-white" />
                        )}
                      </span>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`text-sm font-medium ${s.status === "PENDING" && !s.current ? "text-slate-400" : "text-slate-900"}`}>
                          {i + 1}. {s.label}
                        </span>
                        <Badge variant={s.status === "COMPLETED" ? "success" : s.status === "TERMINATED" ? "critical" : s.current ? "info" : "slate"}>
                          {s.status_label}
                        </Badge>
                        <span className="text-xs text-slate-500">{s.owner_name ? `${s.owner_name} · ` : ""}{s.owner_title}</span>
                      </div>
                      {s.acted_at && (
                        <div className="text-[11px] text-slate-400">{s.acted_by} · {fmtDateTime(s.acted_at)}{s.certificate_no ? ` · ${s.certificate_no}` : ""}</div>
                      )}
                    </li>
                  ))}
                </ol>
                <div className="mt-4">
                  <Link
                    href={`/officer/pipeline/${params.id}`}
                    className="inline-flex items-center gap-1.5 rounded-md bg-navy-700 px-3.5 py-2 text-sm font-medium text-white hover:bg-navy-800"
                  >
                    {t("officer.openPipeline")} →
                  </Link>
                </div>
              </CardContent>
            </Card>
          )}
          {tab === "report" && (<>
            {rep?.officer_summary ? (
              <OfficerSummaryCard s={rep.officer_summary} />
            ) : null}
            {d.doc_scope && d.doc_scope.hidden_count > 0 && (
              <Alert variant="info" title={t("officer.docScopeTitle", { role: ROLE_LABELS[d.my_role ?? ""] ?? d.my_role ?? "" })}>
                {t("officer.docScopeText")}
              </Alert>
            )}
            <ReportTab
              rep={rep}
              onDownload={async () => {
                setMsg(null);
                try {
                  await downloadFile(`/officer/applications/${params.id}/report/download`, `report-${app.application_no}.pdf`);
                } catch (e2: any) {
                  const det = e2?.detail;
                  setMsg({ ok: false, text: typeof det === "string" ? det : det?.message ?? JSON.stringify(det) ?? String(e2) });
                }
              }}
            />
          </>)}
          {tab === "overview" && (
            <div className="space-y-4">
              <Card>
                <CardHeader><CardTitle>{t("officer.bidderProfile")}</CardTitle></CardHeader>
                <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
                  <div><span className="label">Name</span><div>{d.bidder.name}</div></div>
                  <div><span className="label">{t("auth.email")}</span><div>{d.bidder.email}</div></div>
                  <div><span className="label">{t("auth.company")}</span><div>{d.bidder.company}</div></div>
                  <div><span className="label">{t("auth.businessType")}</span><div>{d.bidder.business_type ?? "-"} {d.bidder.business_category ? `· ${d.bidder.business_category}` : ""}</div></div>
                  <div><span className="label">PAN</span><div className="font-mono">{d.bidder.pan_masked ?? "-"}</div></div>
                  <div><span className="label">GSTIN</span><div className="font-mono">{d.bidder.gstin_masked ?? "-"}</div></div>
                  <div><span className="label">Udyam</span><div className="font-mono">{d.bidder.udyam_masked ?? "-"}</div></div>
                  <div><span className="label">{t("common.district")}</span><div>{d.bidder.district} ({d.bidder.state})</div></div>
                  <div className="sm:col-span-2"><span className="label">{t("auth.address")}</span><div>{d.bidder.registered_address ?? "-"}</div></div>
                  <div><span className="label">Turnover</span><div>{d.bidder.annual_turnover ? formatINR(d.bidder.annual_turnover) : "-"}</div></div>
                  <div><span className="label">Filing status</span><div>{d.bidder.filing_status ?? "-"}</div></div>
                  <div><span className="label">Local content</span><div>{d.bidder.local_content_percent ? `${d.bidder.local_content_percent}%` : "-"}</div></div>
                  <div><span className="label">Authorized rep</span><div>{d.bidder.authorized_rep ?? "-"}</div></div>
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle>{t("officer.tenderDetails")}</CardTitle></CardHeader>
                <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
                  <div><span className="label">Tender</span><div>{d.tender.tender_no} - {d.tender.title}</div></div>
                  <div><span className="label">{t("common.district")}</span><div>{d.tender.district}, {d.tender.state}</div></div>
                  <div><span className="label">{t("common.category")}</span><div>{d.tender.category ?? "-"}</div></div>
                  <div><span className="label">Close date</span><div>{fmtDate(d.tender.close_date)}</div></div>
                  <div><span className="label">Required turnover</span><div>{d.tender.required_turnover ? formatINR(d.tender.required_turnover) : "-"}</div></div>
                  <div><span className="label">Min local content</span><div>{d.tender.min_local_content ? `${d.tender.min_local_content}%` : "-"}</div></div>
                  <div className="sm:col-span-2">
                    <span className="label">{t("bidder.procuringOrg")}</span>
                    <div>
                      {d.tender.procuring_organization?.name ?? d.tender.organization ?? "-"}
                      {d.tender.procuring_organization?.procurement_division && (
                        <span className="text-xs text-slate-500"> · {d.tender.procuring_organization.procurement_division}</span>
                      )}
                    </div>
                    {d.tender.authority_chain?.length > 0 && (
                      <div className="mt-1 space-y-0.5">
                        <div className="text-[11px] font-medium text-slate-500">{t("officer.authorityChain")}</div>
                        {d.tender.authority_chain.map((b: any, i: number) => (
                          <div key={i} className="text-xs text-slate-600">
                            <span className="font-medium">{b.band}:</span> {b.review_role_title} → {b.approval_role_title}
                            {b.committee_required && <Badge variant="info" className="ml-1">{t("bidder.tenderCommittee")}</Badge>}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="sm:col-span-2">
                    <span className="label">Routing</span>
                    <div>{app.routing_method ?? "-"} {app.routing_fallback && <Badge variant="warning" className="ml-1">fallback</Badge>}</div>
                  </div>
                  <div className="sm:col-span-2">
                    <span className="label">{t("officer.stageOwner")}</span>
                    <div>
                      {(d.pipeline?.stages ?? []).find((x: any) => x.current) ? (
                        <>
                          {(d.pipeline?.stages ?? []).find((x: any) => x.current)!.label} -{" "}
                          <b>{(d.pipeline?.stages ?? []).find((x: any) => x.current)!.owner_name ?? "-"}</b>
                          {" "}({(d.pipeline?.stages ?? []).find((x: any) => x.current)!.owner_title})
                        </>
                      ) : "-"}
                    </div>
                  </div>
                </CardContent>
              </Card>
              {d.previous_applications.length > 0 && (
                <Card>
                  <CardHeader><CardTitle>{t("officer.previousApplications")}</CardTitle></CardHeader>
                  <CardContent>
                    <table className="w-full text-left text-sm">
                      <thead><tr className="border-b text-xs uppercase text-slate-500"><th className="py-1.5 pr-3">App</th><th className="py-1.5 pr-3">Tender</th><th className="py-1.5 pr-3">{t("common.status")}</th><th className="py-1.5">{t("common.score")}</th></tr></thead>
                      <tbody className="divide-y divide-slate-100">
                        {d.previous_applications.map((p, i) => (
                          <tr key={i}>
                            <td className="py-2 pr-3">{p.application_no}</td>
                            <td className="py-2 pr-3">{p.tender_no}</td>
                            <td className="py-2 pr-3"><AppStatusBadge status={p.status} /></td>
                            <td className="py-2">{p.compliance_score ?? "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </CardContent>
                </Card>
              )}
            </div>
          )}
          {tab === "docs" && (
            <Card>
              <CardHeader>
                <CardTitle>{t("officer.docsTitle")}</CardTitle>
                {app.snapshot_frozen_at && (
                  <CardDescription>
                    {t("bidder.snapshotFrozen")} · {fmtDateTime(app.snapshot_frozen_at)}
                  </CardDescription>
                )}
              </CardHeader>
              <CardContent>
                <table className="w-full text-left text-sm">
                  <thead>
                    <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                      <th className="py-2 pr-3">Document</th>
                      <th className="py-2 pr-3">Source</th>
                      <th className="py-2 pr-3">{t("common.status")}</th>
                      <th className="py-2 pr-3">Expiry</th>
                      <th className="py-2 pr-3">OCR</th>
                      <th className="py-2">{t("common.actions")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {d.documents.map((doc) => (
                      <tr key={doc.id}>
                        <td className="py-2.5 pr-3">
                          <div className="font-medium text-slate-800">{doc.name}</div>
                          <div className="text-xs text-slate-500">{doc.doc_type}{doc.in_application ? " · attached to application" : ""}{doc.notes ? ` · ${doc.notes}` : ""}</div>
                        </td>
                        <td className="py-2.5 pr-3">
                          <Badge variant={doc.source_classification === "GOVERNMENT_SOURCE" ? "default" : "info"}>
                            {doc.source_classification === "GOVERNMENT_SOURCE" ? t("bidder.sourceBadgeGov") : t("bidder.sourceBadgeManual")}
                          </Badge>
                          <div className="mt-0.5 text-[11px] text-slate-500">{doc.masked_identifier ?? ""}</div>
                        </td>
                        <td className="py-2.5 pr-3">
                          <Badge variant={doc.verification_status === "VERIFIED" ? "success" : doc.verification_status === "MANUAL_REVIEW_REQUIRED" || doc.verification_status === "SUSPICIOUS" ? "purple" : "warning"}>
                            {doc.verification_status.replace(/_/g, " ")}
                          </Badge>
                          {doc.authenticity_status && (
                            <div className="mt-1"><Badge variant={authenticityVariant(doc.authenticity_status)}>{doc.authenticity_status.replace(/_/g, " ")}</Badge></div>
                          )}
                        </td>
                        <td className="py-2.5 pr-3 text-xs">{doc.expiry_date ? fmtDate(doc.expiry_date) : "-"}</td>
                        <td className="py-2.5 pr-3 text-xs">{doc.ocr_confidence != null ? `${doc.ocr_confidence}%` : "-"}</td>
                        <td className="py-2.5">
                          <Button size="sm" variant="outline" onClick={() => setViewing({ kind: "officer", docId: doc.id, docName: doc.name, applicationId: params.id })}>
                            <Eye size={12} /> {t("bidder.viewDocument")}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {d.documents.length === 0 && <p className="py-4 text-sm text-slate-500">{t("common.noData")}</p>}
              </CardContent>
            </Card>
          )}
          {tab === "clar" && (
            <Card>
              <CardHeader>
                <CardTitle>{t("bidder.clarificationRequests")}</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {d.clarifications.map((c) => (
                  <div key={c.id} className="rounded-md border border-slate-200 p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium text-slate-800">{c.reason}</span>
                      <Badge variant={c.status === "OPEN" ? "warning" : "slate"}>{c.status}</Badge>
                    </div>
                    {c.documents_needed?.length ? <p className="mt-1 text-xs text-slate-500">{c.documents_needed.join(", ")}</p> : null}
                    <p className="mt-1 text-xs text-slate-500">Due {c.deadline ? fmtDate(c.deadline) : "-"} · created {fmtDateTime(c.created_at)}</p>
                    {c.response_text && <p className="mt-1 rounded bg-slate-50 p-2 text-xs text-slate-700">Bidder response: {c.response_text}</p>}
                  </div>
                ))}
                {d.clarifications.length === 0 && <p className="text-sm text-slate-500">{t("common.noData")}</p>}
              </CardContent>
            </Card>
          )}
          {tab === "decisions" && (
            <div className="space-y-4">
              <Card>
                <CardHeader><CardTitle>Officer decisions</CardTitle></CardHeader>
                <CardContent>
                  {d.decisions.length === 0 && <p className="text-sm text-slate-500">{t("common.noData")}</p>}
                  <div className="divide-y divide-slate-100">
                    {d.decisions.map((x) => (
                      <div key={x.id} className="flex items-center justify-between py-2.5">
                        <div>
                          <span className="text-sm font-medium text-slate-800">{x.action}</span>
                          <span className="ml-2 text-xs text-slate-500">{x.officer} · {fmtDateTime(x.at)}</span>
                          {x.is_overridden && <Badge variant="warning" className="ml-2">OVERRIDDEN</Badge>}
                        </div>
                        {x.remarks && <span className="max-w-[50%] truncate text-xs text-slate-500">{x.remarks}</span>}
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle>Overrides (accountability chain)</CardTitle></CardHeader>
                <CardContent>
                  {d.overrides.length === 0 && <p className="text-sm text-slate-500">{t("common.noData")}</p>}
                  <div className="divide-y divide-slate-100">
                    {d.overrides.map((o) => (
                      <div key={o.id} className="py-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge variant={riskVariant(o.severity)}>{o.severity}</Badge>
                          <Badge variant={o.status === "PENDING_COSIGN" ? "critical" : "success"}>{o.status}</Badge>
                          <span className="text-xs text-slate-500">{o.officer} · {fmtDateTime(o.at)}</span>
                        </div>
                        <p className="mt-1 text-sm text-slate-700">
                          <b>Category:</b> {o.reason_category} - {o.justification}
                        </p>
                        {o.admin_cosign_required && (
                          <p className="mt-1 text-xs">
                            {o.admin_cosigned_at ? (
                              <span className="text-emerald-700">Admin co-signed {fmtDateTime(o.admin_cosigned_at)}</span>
                            ) : (
                              <span className="text-critical-700">Admin co-sign pending</span>
                            )}
                            {o.vigilance_referral_id && <span className="ml-2 text-purple-700">Vigilance referral: {o.vigilance_referral_id}</span>}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle>{t("common.timeline")}</CardTitle></CardHeader>
                <CardContent>
                  <ol className="relative ml-2 space-y-4 border-l border-slate-200 pl-5">
                    {[...d.timeline].reverse().map((h, i) => (
                      <li key={i} className="relative">
                        <span className="absolute -left-[26px] top-1 h-3 w-3 rounded-full border-2 border-white bg-navy-700" />
                        <div className="text-sm font-medium text-slate-900">{h.status}</div>
                        <div className="text-xs text-slate-500">{h.actor_role} · {fmtDateTime(h.at)}</div>
                        {h.remarks && <div className="text-xs text-slate-600">{h.remarks}</div>}
                      </li>
                    ))}
                  </ol>
                </CardContent>
              </Card>
            </div>
          )}
          {tab === "audit" && (
            <Card>
              <CardHeader>
                <CardTitle>{t("common.audit")} (hash-chained)</CardTitle>
                <CardDescription>Immutable records: each record hashes the previous one.</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-slate-200 uppercase tracking-wide text-slate-500">
                        <th className="py-2 pr-3">Action</th>
                        <th className="py-2 pr-3">Actor</th>
                        <th className="py-2 pr-3">At</th>
                        <th className="py-2 pr-3">Record hash</th>
                        <th className="py-2">Prev hash</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {d.audit_trail.map((x) => (
                        <tr key={x.id}>
                          <td className="py-2 pr-3 font-medium text-slate-800">{x.action}</td>
                          <td className="py-2 pr-3">{x.actor_role}</td>
                          <td className="py-2 pr-3 whitespace-nowrap">{fmtDateTime(x.at)}</td>
                          <td className="py-2 pr-3 font-mono text-[10px] text-slate-500">{x.record_hash?.slice(0, 14)}…</td>
                          <td className="py-2 font-mono text-[10px] text-slate-500">{x.prev_hash?.slice(0, 14)}…</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Pipeline progression panel */}
        <div className="space-y-4">
          <Card className="sticky top-20">
            <CardHeader>
              <CardTitle>{t("officer.pipelineProgress")}</CardTitle>
              <CardDescription>{t("officer.pipelineProgressHint")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {(() => {
                const cur = (d.pipeline?.stages ?? []).find((x: any) => x.current);
                if (!cur) {
                  return <Alert variant="success">{t("officer.pipelineDoneText")}</Alert>;
                }
                return (
                  <>
                    <div className="rounded-md border border-navy-200 bg-navy-50/50 p-3">
                      <div className="text-[11px] font-semibold uppercase tracking-wide text-navy-600">
                        {t("officer.currentStage")}
                      </div>
                      <div className="mt-1 text-sm font-semibold text-navy-900">{cur.label}</div>
                      <div className="mt-1 text-xs text-slate-600">
                        {t("officer.stageOwner")}: <b>{cur.owner_name ?? "- "}</b>{" "}
                        <span className="text-slate-500">({cur.owner_title})</span>
                      </div>
                    </div>
                    <Link
                      href={`/officer/pipeline/${params.id}`}
                      className="flex w-full items-center justify-center gap-1.5 rounded-md bg-navy-700 px-3.5 py-2.5 text-sm font-semibold text-white hover:bg-navy-800"
                    >
                      {t("officer.openPipeline")} →
                    </Link>
                    <p className="text-[11px] leading-snug text-slate-500">
                      {t("officer.pipelinePanelNote")}
                    </p>
                    <div className="border-t border-slate-100 pt-2">
                      <Button variant="warning" className="w-full" disabled={!canAct}
                        onClick={() => { setDecOpen("clarification"); setDecReason(""); setDecDocs(""); setDecDays("3"); }}>
                        {t("officer.requestClarification")}
                      </Button>
                      <Button variant="critical" className="mt-2 w-full" disabled={!canAct} onClick={() => decide("FLAG_SUSPECTED_FRAUD")}>
                        {t("officer.flagFraud")}
                      </Button>
                    </div>
                  </>
                );
              })()}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Clarification dialog */}
      <Dialog open={decOpen === "clarification"} onClose={() => setDecOpen(null)} title={t("officer.requestClarification")}>
        <div className="space-y-4">
          <Field label={t("officer.clarificationReason")} required>
            <Textarea required value={decReason} onChange={(e) => setDecReason(e.target.value)} />
          </Field>
          <Field label={t("officer.documentsNeeded")} hint="Comma separated, e.g. GST_CERT, UDYAM_CERT">
            <Input value={decDocs} onChange={(e) => setDecDocs(e.target.value)} />
          </Field>
          <Field label={t("officer.deadlineDays")}>
            <Input type="number" min={1} max={30} value={decDays} onChange={(e) => setDecDays(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDecOpen(null)}>{t("common.cancel")}</Button>
            <Button variant="warning" onClick={() => decide("REQUEST_CLARIFICATION")}>{t("common.submit")}</Button>
          </div>
        </div>
      </Dialog>

      <DocViewer target={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}
