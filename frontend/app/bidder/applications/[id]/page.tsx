"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Download, FileWarning, RotateCcw, Send, Upload } from "lucide-react";
import { api, downloadFile, upload } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/fields";
import { Loading, AppStatusBadge, RiskBadge, EligibilityBadge } from "@/components/app/widgets";
import { fmtDate, fmtDateTime, formatAmount } from "@/lib/utils";

interface RejectionExplanation {
  application_no: string;
  tender_no: string;
  tender_title: string;
  rejected_at: string | null;
  decided_by: string;
  explanation: string;
  appeal_note: string;
}

interface FinancialEnvelope {
  required: boolean;
  status: string;
  version: number | null;
  sealed_at: string | null;
  opened: boolean;
  financial_bid_hash: string | null;
  currency?: string;
  base_amount?: string;
  tax_amount?: string;
  other_charges?: string;
  total_quoted_amount?: string;
  price_valid_days?: number;
  price_valid_until?: string | null;
  commercial_declaration?: string;
}

interface Detail {
  financial_envelope?: FinancialEnvelope;
  id: string;
  application_no: string;
  status: string;
  tender: { tender_no: string; title: string; close_date: string };
  eligibility_result: string | null;
  compliance_score: number | null;
  risk_level: string | null;
  risk_label: string | null;
  report_stale: boolean;
  submitted_at: string | null;
  declaration: string | null;
  timeline: Array<{ status: string; remarks: string | null; at: string; actor_role: string }>;
  next_step: string;
  report_summary: {
    generated_at: string;
    risk_level: string;
    eligibility_result: string;
    compliance_score: number;
    ai_explanation: string | null;
    mismatches: Array<{ field: string; bidder_value: string; source_value: string; source: string }>;
    missing_documents: string[];
    blacklist_findings: Record<string, unknown>[];
  } | null;
  clarifications: Array<{
    id: string;
    reason: string;
    documents_needed: string[] | null;
    deadline: string | null;
    status: string;
    response_text: string | null;
    responded_at: string | null;
    created_at: string;
  }>;
}

export default function ApplicationDetailPage() {
  const t = useTranslations();
  const params = useParams<{ id: string }>();
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState("");

  const [respOpen, setRespOpen] = useState<string | null>(null);
  const [respText, setRespText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [grievOpen, setGrievOpen] = useState(false);
  const [grievReason, setGrievReason] = useState("");
  const [grievDetails, setGrievDetails] = useState("");
  const [rej, setRej] = useState<RejectionExplanation | null>(null);
  const [rejBusy, setRejBusy] = useState(false);

  const load = () =>
    api<Detail>(`/bidder/applications/${params.id}`).then(setD).catch((e) => setErr(e?.detail ?? String(e)));
  useEffect(() => {
    load();
  }, [params.id]);

  // Rejection explanation (only when the outcome is Rejected)
  useEffect(() => {
    if (d && d.status === "Rejected") {
      api<RejectionExplanation>(`/bidder/applications/${d.id}/rejection-explanation`)
        .then(setRej)
        .catch(() => setRej(null));
    } else {
      setRej(null);
    }
  }, [d?.status, d?.id]);

  const downloadRejection = async () => {
    setRejBusy(true);
    try {
      await downloadFile(`/bidder/applications/${d!.id}/rejection-explanation/download`, `rejection-${d!.application_no}.pdf`);
    } catch (e2: any) {
      const det = e2?.detail;
      setMsg({ ok: false, text: typeof det === "string" ? det : det?.message ?? String(e2) });
    } finally {
      setRejBusy(false);
    }
  };

  const downloadSubmissionCert = async () => {
    setRejBusy(true);
    try {
      await downloadFile(`/bidder/applications/${d!.id}/certificate/submission`, `submission-certificate-${d!.application_no}.pdf`);
    } catch (e2: any) {
      const det = e2?.detail;
      setMsg({ ok: false, text: typeof det === "string" ? det : det?.message ?? String(e2) });
    } finally {
      setRejBusy(false);
    }
  };

  if (err) return <Alert variant="danger">{err}</Alert>;
  if (!d) return <Loading />;

  const openClar = d.clarifications.find((c) => c.status === "OPEN");

  const respond = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      if (file) {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("clarification_id", respOpen!);
        fd.append("response_text", respText);
        await upload(`/bidder/applications/${d.id}/clarification-response/upload`, fd);
      } else {
        await api(`/bidder/applications/${d.id}/clarification-response`, {
          method: "POST",
          body: JSON.stringify({ clarification_id: respOpen!, response_text: respText }),
        });
      }
      setRespOpen(null);
      setRespText("");
      setFile(null);
      setMsg({ ok: true, text: "Response submitted - application moved to Resubmitted." });
      load();
    } catch (e2: any) {
      setMsg({ ok: false, text: e2?.detail ?? String(e2) });
    } finally {
      setBusy(false);
    }
  };

  const withdraw = async () => {
    if (!window.confirm(t("bidder.withdrawConfirm"))) return;
    try {
      await api(`/bidder/applications/${d.id}/withdraw`, { method: "POST" });
      setMsg({ ok: true, text: "Application withdrawn." });
      load();
    } catch (e2: any) {
      setMsg({ ok: false, text: e2?.detail ?? String(e2) });
    }
  };

  const fileGrievance = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api(`/bidder/grievances`, {
        method: "POST",
        body: JSON.stringify({ application_id: d.id, reason: grievReason, details: grievDetails || undefined }),
      });
      setGrievOpen(false);
      setMsg({ ok: true, text: "Grievance/appeal filed. Admin has been notified." });
    } catch (e2: any) {
      setMsg({ ok: false, text: e2?.detail ?? String(e2) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/bidder/applications" className="text-sm text-navy-700 hover:underline">← {t("nav.applications")}</Link>
          <h1 className="mt-1 text-xl font-bold text-navy-900">{d.application_no}</h1>
          <p className="text-sm text-slate-500">{d.tender.tender_no} - {d.tender.title}</p>
        </div>
        <div className="flex items-center gap-2">
          <AppStatusBadge status={d.status} />
          <RiskBadge risk={d.risk_level} />
          {d.report_stale && <Badge variant="warning">Report stale - new verification pending</Badge>}
        </div>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

      {rej && (
        <Card className="border-red-200">
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle className="text-red-800">{t("bidder.rejectionTitle")}</CardTitle>
              <p className="text-xs text-slate-500">
                {t("bidder.rejectionDecidedBy")}: {rej.decided_by}
                {rej.rejected_at ? ` · ${fmtDateTime(rej.rejected_at)}` : ""}
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={downloadRejection} loading={rejBusy}>
              <Download size={13} /> {t("bidder.rejectionDownload")}
            </Button>
          </CardHeader>
          <CardContent>
            <p className="whitespace-pre-line rounded-md bg-red-50/60 p-3 text-sm text-slate-700">{rej.explanation}</p>
            <p className="mt-2 text-xs text-slate-500">{rej.appeal_note}</p>
          </CardContent>
        </Card>
      )}

      {openClar && (
        <Alert variant="warning" title={`${t("bidder.clarificationRequests")} (due ${openClar.deadline ? fmtDate(openClar.deadline) : "-"})`}>
          <p>{openClar.reason}</p>
          {openClar.documents_needed?.length ? (
            <p className="mt-1 text-xs">{t("officer.documentsNeeded")}: {openClar.documents_needed.join(", ")}</p>
          ) : null}
          <Button size="sm" variant="warning" className="mt-2" onClick={() => { setRespOpen(openClar.id); setRespText(""); setFile(null); }}>
            <Send size={13} /> {t("bidder.respondClarification")}
          </Button>
        </Alert>
      )}

      {d.financial_envelope?.required && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Financial Quotation</CardTitle>
            <Badge variant={d.financial_envelope.opened ? "success" : "slate"}>
              {d.financial_envelope.opened ? "Opened" : "Sealed"}
            </Badge>
          </CardHeader>
          <CardContent>
            {d.financial_envelope.total_quoted_amount ? (
              <>
                <div className="grid gap-4 sm:grid-cols-4">
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-slate-500">Base amount</div>
                    <div className="tabular-nums font-medium text-slate-800">
                      {formatAmount(d.financial_envelope.base_amount)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-slate-500">Tax</div>
                    <div className="tabular-nums font-medium text-slate-800">
                      {formatAmount(d.financial_envelope.tax_amount)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-slate-500">Other charges</div>
                    <div className="tabular-nums font-medium text-slate-800">
                      {formatAmount(d.financial_envelope.other_charges)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-slate-500">Total quoted</div>
                    <div className="tabular-nums text-lg font-bold text-navy-900">
                      {formatAmount(d.financial_envelope.total_quoted_amount)}
                    </div>
                  </div>
                </div>
                <div className="mt-3 space-y-1 text-xs text-slate-600">
                  <p>
                    Sealed on {fmtDateTime(d.financial_envelope.sealed_at)} (version{" "}
                    {d.financial_envelope.version}). Price valid for{" "}
                    {d.financial_envelope.price_valid_days} days
                    {d.financial_envelope.price_valid_until
                      ? ` (until ${fmtDate(d.financial_envelope.price_valid_until)})`
                      : ""}
                    .
                  </p>
                  <p>
                    Only you can see this amount until the authorised bid opening. Integrity hash:{" "}
                    <span className="font-mono">{d.financial_envelope.financial_bid_hash?.slice(0, 16)}</span>
                  </p>
                </div>
              </>
            ) : (
              <p className="text-sm text-slate-500">
                No financial quotation was recorded for this application.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>{t("common.timeline")}</CardTitle>
            <p className="text-xs text-slate-500">
              {t("bidder.nextStep")}: {d.next_step}
            </p>
          </CardHeader>
          <CardContent>
            <ol className="relative ml-2 space-y-5 border-l border-slate-200 pl-5">
              {[...d.timeline].reverse().map((h, i) => (
                <li key={i} className="relative">
                  <span className="absolute -left-[26px] top-1 h-3 w-3 rounded-full border-2 border-white bg-navy-700" />
                  <div className="text-sm font-semibold text-slate-900">{h.status}</div>
                  <div className="text-xs text-slate-500">
                    {h.actor_role} · {fmtDateTime(h.at)}
                  </div>
                  {h.remarks && <div className="mt-0.5 text-xs text-slate-600">{h.remarks}</div>}
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>{t("common.details")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <div><span className="label">{t("common.eligibility")}</span><EligibilityBadge value={d.eligibility_result ?? ""} /></div>
              <div className="flex items-center justify-between"><span className="label">{t("common.score")}</span><b>{d.compliance_score ?? "-"}</b></div>
              <div className="flex items-center justify-between"><span className="label">Risk</span><RiskBadge risk={d.risk_level} /></div>
              {d.risk_label && <div className="text-xs text-slate-500">{d.risk_label}</div>}
              <div className="flex items-center justify-between"><span className="label">Submitted</span><span>{d.submitted_at ? fmtDateTime(d.submitted_at) : "-"}</span></div>
              <div className="flex items-center justify-between"><span className="label">{t("common.tender")}</span><span>{d.tender.tender_no}</span></div>
              <div className="border-t border-slate-100 pt-2">
                <Button size="sm" variant="outline" className="w-full" onClick={downloadSubmissionCert} loading={rejBusy}>
                  <Download size={13} /> {t("bidder.submissionCertificate")}
                </Button>
                <p className="mt-1.5 text-[11px] text-slate-500">{t("bidder.submissionCertHint")}</p>
              </div>
            </CardContent>
            <CardFooterActions onWithdraw={withdraw} canWithdraw={["AI Verification Not Run", "AI Verification In Progress", "Pending L1 Review"].includes(d.status)} onGrievance={() => setGrievOpen(true)} canGrieve={d.status === "Rejected" || d.status === "Closed"} />
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Declaration</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="whitespace-pre-line text-xs text-slate-600">{d.declaration || "-"}</p>
            </CardContent>
          </Card>
        </div>
      </div>

      {d.report_summary && (
        <Card>
          <CardHeader>
            <CardTitle>Verification summary (shareable)</CardTitle>
            <p className="text-xs text-slate-500">Generated {fmtDateTime(d.report_summary.generated_at)}</p>
          </CardHeader>
          <CardContent className="space-y-3">
            {d.report_summary.ai_explanation && (
              <p className="rounded-md bg-slate-50 p-3 text-sm text-slate-700">{d.report_summary.ai_explanation}</p>
            )}
            {d.report_summary.mismatches.length > 0 && (
              <div>
                <div className="label mb-1">Mismatches</div>
                {d.report_summary.mismatches.map((m, i) => (
                  <p key={i} className="text-xs text-red-700">
                    {m.field}: “{m.bidder_value}” vs source “{m.source_value}” ({m.source})
                  </p>
                ))}
              </div>
            )}
            {d.report_summary.missing_documents.length > 0 && (
              <div>
                <div className="label mb-1">Missing documents</div>
                <ul className="list-disc pl-5 text-xs text-slate-600">
                  {d.report_summary.missing_documents.map((m, i) => (
                    <li key={i}>{m}</li>
                  ))}
                </ul>
              </div>
            )}
            {d.report_summary.blacklist_findings.length > 0 && (
              <Alert variant="critical" title="Blacklist findings">
                <ul className="list-disc pl-5 text-xs">
                  {d.report_summary.blacklist_findings.map((b, i) => (
                    <li key={i}>{JSON.stringify(b)}</li>
                  ))}
                </ul>
              </Alert>
            )}
          </CardContent>
        </Card>
      )}

      {d.clarifications.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t("bidder.clarificationRequests")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {d.clarifications.map((c) => (
              <div key={c.id} className="rounded-md border border-slate-200 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-800">{c.reason}</span>
                  <Badge variant={c.status === "OPEN" ? "warning" : c.status === "RESPONDED" ? "info" : "slate"}>{c.status}</Badge>
                </div>
                {c.documents_needed?.length ? <p className="mt-1 text-xs text-slate-500">{c.documents_needed.join(", ")}</p> : null}
                {c.response_text && (
                  <p className="mt-1 rounded bg-slate-50 p-2 text-xs text-slate-700">
                    Your response ({fmtDateTime(c.responded_at!)}): {c.response_text}
                  </p>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Dialog open={respOpen !== null} onClose={() => setRespOpen(null)} title={t("bidder.respondClarification")}>
        <form onSubmit={respond} className="space-y-4">
          <Field label={t("bidder.clarificationResponse")} required>
            <Textarea required minLength={5} value={respText} onChange={(e) => setRespText(e.target.value)} />
          </Field>
          <Field label={`${t("bidder.uploadCorrection")} (optional)`}>
            <Input type="file" accept=".pdf,.jpg,.jpeg,.png" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setRespOpen(null)}>{t("common.cancel")}</Button>
            <Button type="submit" loading={busy}>{t("common.submit")}</Button>
          </div>
        </form>
      </Dialog>

      <Dialog open={grievOpen} onClose={() => setGrievOpen(false)} title={t("bidder.fileGrievance")}>
        <form onSubmit={fileGrievance} className="space-y-4">
          <Field label={t("bidder.grievanceReason")} required>
            <Select required value={grievReason} onChange={(e) => setGrievReason(e.target.value)}>
              <option value="">-</option>
              <option>Decision is in error</option>
              <option>Incorrect or outdated data was used</option>
              <option>Documents were not properly considered</option>
              <option>Process/SLA violation</option>
              <option>Other</option>
            </Select>
          </Field>
          <Field label={t("bidder.grievanceDetails")}>
            <Textarea value={grievDetails} onChange={(e) => setGrievDetails(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setGrievOpen(false)}>{t("common.cancel")}</Button>
            <Button type="submit" loading={busy}>{t("common.submit")}</Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}

function CardFooterActions({
  onWithdraw,
  canWithdraw,
  onGrievance,
  canGrieve,
}: {
  onWithdraw: () => void;
  canWithdraw: boolean;
  onGrievance: () => void;
  canGrieve: boolean;
}) {
  const t = useTranslations();
  return (
    <div className="flex gap-2 border-t border-slate-100 px-5 py-3">
      {canWithdraw && (
        <Button size="sm" variant="outline" onClick={onWithdraw}>
          <RotateCcw size={13} /> {t("bidder.withdraw")}
        </Button>
      )}
      {canGrieve && (
        <Button size="sm" variant="warning" onClick={onGrievance}>
          <FileWarning size={13} /> {t("bidder.fileGrievance")}
        </Button>
      )}
    </div>
  );
}
