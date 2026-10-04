"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  CheckCircle2, Download, FileBadge, Lock, Play,
} from "lucide-react";
import { api, downloadFile } from "@/lib/api";
import type { PipelineState, OverrideChallenge } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Field, Input, Select, Textarea } from "@/components/ui/fields";
import { Loading, AppStatusBadge, RiskBadge } from "@/components/app/widgets";
import { OverrideModal } from "@/components/app/override-modal";
import { fmtDateTime } from "@/lib/utils";

type FormState = Record<string, string>;

const ROLE_LABELS: Record<string, string> = {
  BIDDER: "Bidder",
  BID_OPENING_OFFICER: "Bid Opening Officer",
  PROCUREMENT_OFFICER: "Bid Compliance Officer (AI Screening)",
  TECHNICAL_EVALUATOR: "Technical Evaluation Officer / Committee",
  FINANCE_EVALUATOR: "Financial Evaluation Officer / Committee",
  CFA: "Tender Approving Authority (Competent Authority)",
  CONTRACT_MANAGER: "Contract Manager",
  CONTRACT_OFFICER: "Contract Execution / Project Officer",
  INSPECTION_OFFICER: "Delivery & Inspection / Consignee Officer",
  PAYMENT_OFFICER: "Payment & Settlement / Accounts Officer",
  AUDITOR: "Audit / Compliance Officer",
  ADMIN: "System Admin",
};

const OUTCOME_LABELS: Record<string, string> = {
  OPEN_BIDS: "Close & open bids for evaluation",
  VERIFIED: "Compliance Verified",
  MANUAL_REVIEW_REQUIRED: "Manual Review Required",
  NON_COMPLIANT: "Non-Compliant (reject at screening)",
  TECHNICALLY_QUALIFIED: "Technically Qualified",
  CLARIFICATION_REQUIRED: "Clarification Required",
  TECHNICALLY_DISQUALIFIED: "Technically Disqualified",
  PASS: "Financially Qualified (record amount & L-rank)",
  FAIL: "Financial Evaluation Failed",
  APPROVE: "Approve & issue Letter of Award",
  REJECT: "Reject (do not award)",
  ISSUE_CONTRACT: "Issue Contract / Purchase Order",
  RECORD_PROGRESS: "Record execution milestones",
  ACCEPTED: "Delivery Accepted",
  PARTIALLY_ACCEPTED: "Partially Accepted",
  REJECTED: "Delivery Rejected",
  RECTIFICATION_REQUIRED: "Rectification Required (re-inspect later)",
  SETTLE_PAYMENT: "Verify invoice & settle payment",
  CLOSE_AUDIT: "Verify audit trail & close",
};

function StageIcon({ status, current }: { status: string; current: boolean }) {
  if (status === "COMPLETED")
    return <span className="flex h-7 w-7 items-center justify-center rounded-full bg-success-100 text-success-700"><CheckCircle2 size={16} /></span>;
  if (status === "TERMINATED")
    return <span className="flex h-7 w-7 items-center justify-center rounded-full bg-critical-100 text-critical-700">✕</span>;
  if (current)
    return <span className="flex h-7 w-7 items-center justify-center rounded-full bg-navy-700 text-white"><Play size={13} /></span>;
  return <span className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-slate-200 bg-white text-slate-300 text-xs">·</span>;
}

export default function OfficerPipelinePage() {
  const t = useTranslations();
  const params = useParams<{ id: string }>();
  const [d, setD] = useState<PipelineState | null>(null);
  const [err, setErr] = useState("");
  const [form, setForm] = useState<FormState>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [challenge, setChallenge] = useState<OverrideChallenge | null>(null);
  const [milestones, setMilestones] = useState<string[]>([""]);

  const load = useCallback(() => {
    api<PipelineState>(`/officer/pipeline/${params.id}`)
      .then(setD)
      .catch((e) => setErr(typeof e?.detail === "string" ? e.detail : String(e)));
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  if (err) return <Alert variant="danger">{err}</Alert>;
  if (!d) return <Loading />;

  const cur = d.stages.find((s) => s.current);
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async () => {
    if (!cur) return;
    setBusy(true);
    setMsg(null);
    try {
      const body: Record<string, unknown> = { action: form.action, remarks: form.remarks };
      if (cur.key === "COMPLIANCE") body.finding = form.action;
      if (cur.key === "TECHNICAL") body.outcome = form.action;
      if (cur.key === "DELIVERY") { body.outcome = form.action; body.batch_serial = form.batch_serial || undefined; }
      if (cur.key === "FINANCIAL") {
        body.outcome = form.action;
        body.amount = Number(form.amount) || undefined;
        if (form.tax_percent) body.tax_percent = Number(form.tax_percent);
      }
      if (cur.key === "APPROVAL") body.decision = form.action;
      if (cur.key === "CONTRACT") { body.contract_no = form.contract_no; body.po_no = form.po_no || undefined; }
      if (cur.key === "EXECUTION")
        body.milestones = milestones.filter(Boolean).map((m) => {
          const [name, due] = m.split("|").map((x) => x.trim());
          return { name, due: due || undefined, status: "ON_TRACK" };
        });
      if (cur.key === "PAYMENT") {
        body.invoice_no = form.invoice_no || undefined;
        body.payment_ref = form.payment_ref;
        body.payment_amount = form.payment_amount ? Number(form.payment_amount) : undefined;
      }
      const r = await api<{ certificate_no: string | null }>(`/officer/pipeline/${params.id}/${cur.key}/action`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setMsg({ ok: true, text: `Stage completed. ${t("officer.certIssued")} ${r.certificate_no}` });
      setForm({});
      setMilestones([""]);
      load();
    } catch (e2: any) {
      const det = e2?.detail;
      if (e2?.status === 409 && typeof det === "object" && det && (det as any).override_required) {
        setChallenge(det as OverrideChallenge);
      } else {
        setMsg({ ok: false, text: typeof det === "string" ? det : det?.message ?? JSON.stringify(det) ?? String(e2) });
      }
    } finally {
      setBusy(false);
    }
  };

  const downloadCert = async (stage: string, no: string) => {
    try {
      await downloadFile(`/officer/pipeline/${params.id}/certificate/${stage}`, `${no}.pdf`);
    } catch (e: any) {
      setMsg({ ok: false, text: typeof e?.detail === "string" ? e.detail : String(e) });
    }
  };

  const needsRemarks = true;
  const actionOk =
    !!form.action &&
    (!needsRemarks || !!form.remarks) &&
    (cur?.key !== "FINANCIAL" || (form.action === "PASS" && !!form.amount)) &&
    (cur?.key !== "CONTRACT" || !!form.contract_no) &&
    (cur?.key !== "PAYMENT" || !!form.payment_ref);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href={`/officer/review/${params.id}`} className="text-sm text-navy-700 hover:underline">
            ← {t("nav.queue")}
          </Link>
          <h1 className="mt-1 text-xl font-bold text-navy-900">
            {t("officer.pipeline")} - {d.application_no}
          </h1>
          <p className="text-sm text-slate-500">{d.tender_no} · {d.tender_title}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <AppStatusBadge status={d.status} />
            <Badge variant="info">{t("officer.yourRole")}: {ROLE_LABELS[d.my_role] ?? d.my_role}</Badge>
            {d.doc_scope.hidden_count > 0 && (
              <Badge variant="default">
                {t("officer.docScopeBanner", { count: d.doc_scope.hidden_count })}
              </Badge>
            )}
          </div>
        </div>
        <Badge variant={d.current_stage ? "warning" : "success"}>
          {d.current_stage ? `${t("officer.currentStage")}: ${cur?.label}` : t("officer.pipelineComplete")}
        </Badge>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

      {d.doc_scope.hidden_count > 0 && (
        <Alert variant="info" title={t("officer.docScopeTitle", { role: ROLE_LABELS[d.my_role] ?? d.my_role })}>
          {t("officer.docScopeText")}
        </Alert>
      )}

      <div className="grid gap-6 xl:grid-cols-3">
        {/* stepper */}
        <div className="xl:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle>{t("officer.processFlow")}</CardTitle>
              <CardDescription>{t("officer.processFlowHint")}</CardDescription>
            </CardHeader>
            <CardContent>
              <ol className="relative ml-2 space-y-1 border-l border-slate-200 pl-6">
                {d.stages.map((s, i) => (
                  <li key={s.key} className="relative pb-5">
                    <span className="absolute -left-[34px] top-0">
                      <StageIcon status={s.status} current={s.current} />
                    </span>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`text-sm font-semibold ${s.status === "PENDING" && !s.current ? "text-slate-400" : "text-slate-900"}`}>
                        {i + 1}. {s.label}
                      </span>
                      <Badge variant={s.status === "COMPLETED" ? "success" : s.status === "TERMINATED" ? "critical" : s.current ? "info" : "slate"}>
                        {s.status_label}
                      </Badge>
                      {s.certificate_no && (
                        <Button size="sm" variant="outline" onClick={() => downloadCert(s.key, s.certificate_no!)}>
                          <FileBadge size={12} /> {s.certificate_no}
                        </Button>
                      )}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">
                      {s.owner_name ? <>{t("officer.stageOwner")}: <b className="text-slate-700">{s.owner_name}</b> · </> : null}
                      {s.owner_title}
                      {s.acted_by && <> · {t("officer.completedBy")}: {s.acted_by}</>}
                      {s.acted_at && <> · {fmtDateTime(s.acted_at)}</>}
                    </div>
                    {s.payload && (
                      <div className="mt-1.5 rounded-md bg-slate-50 p-2 text-xs text-slate-600">
                        {s.key === "COMPLIANCE" && (
                          <>Score <b>{s.payload.score}/100</b> · Risk <b>{s.payload.risk}</b> · {s.payload.finding}
                            {s.payload.report_run_no ? <> · Run {s.payload.report_run_no}</> : null}</>
                        )}
                        {s.key === "FINANCIAL" && (
                          <>Amount <b>{Number(s.payload.amount).toLocaleString("en-IN")}</b> INR · Rank <b>L{s.payload.rank}</b> · {s.payload.outcome}</>
                        )}
                        {s.key === "APPROVAL" && <>Decision: <b>{s.payload.decision}</b></>}
                        {s.key === "CONTRACT" && <>Contract {s.payload.contract_no}{s.payload.po_no ? ` · PO ${s.payload.po_no}` : ""}</>}
                        {s.key === "EXECUTION" && (
                          <>Milestones: {(s.payload.milestones as any[] | undefined)?.map((m) => `${m.name} (${m.status})`).join(", ") || "-"}</>
                        )}
                        {s.key === "DELIVERY" && <>Outcome: <b>{s.payload.outcome}</b>{s.payload.batch_serial ? ` · ${s.payload.batch_serial}` : ""}</>}
                        {s.key === "PAYMENT" && (
                          <>Ref <b>{s.payload.payment_ref}</b> · {Number(s.payload.payment_amount).toLocaleString("en-IN")} INR {s.payload.invoice_no ? `· Inv ${s.payload.invoice_no}` : ""}</>
                        )}
                        {s.key === "AUDIT" && (
                          <>Audit chain: <b className={s.payload.audit_chain_valid ? "text-success-700" : "text-critical-700"}>{s.payload.audit_chain_valid ? "VALID" : "INVALID"}</b> · {s.payload.audit_records} records</>
                        )}
                        {s.payload.remarks && <div className="mt-1 italic text-slate-500">“{s.payload.remarks}”</div>}
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            </CardContent>
          </Card>
        </div>

        {/* action panel */}
        <div className="space-y-4">
          <Card className="sticky top-20">
            <CardHeader>
              <CardTitle>{cur ? t("officer.actOnStage") : t("officer.pipelineComplete")}</CardTitle>
              <CardDescription>
                {cur ? `${cur.label} - ${cur.owner_name ?? (ROLE_LABELS[cur.owner_role] ?? cur.owner_role)} (${cur.owner_title})` : ""}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {cur ? (
                <>
                  {d.my_role !== cur.owner_role && d.my_role !== "ADMIN" && (
                    <Alert variant="warning" title={t("officer.roleMismatch")}>
                      {t("officer.roleMismatchText", { role: ROLE_LABELS[cur.owner_role] ?? cur.owner_role })}
                    </Alert>
                  )}
                  {cur.key === "COMPLIANCE" && (
                    <p className="text-xs text-slate-500">
                      {t("officer.complianceHint")}
                    </p>
                  )}
                  <Field label={t("common.action")} required>
                    <Select value={form.action} onChange={(e) => set("action", e.target.value)}>
                      <option value="">{t("common.select")}</option>
                      {cur.actions.map((a) => (
                        <option key={a} value={a}>{OUTCOME_LABELS[a] ?? a}</option>
                      ))}
                    </Select>
                  </Field>
                  {cur.key === "FINANCIAL" && form.action === "PASS" && (
                    <>
                      <Field label={t("officer.evalAmount")} required>
                        <Input type="number" min={1} value={form.amount} onChange={(e) => set("amount", e.target.value)} placeholder="72000000" />
                      </Field>
                      <Field label={t("officer.taxPercent")} hint="Optional - recorded for the financial record">
                        <Input type="number" min={0} max={100} step="0.5" value={form.tax_percent} onChange={(e) => set("tax_percent", e.target.value)} placeholder="18" />
                      </Field>
                    </>
                  )}
                  {cur.key === "CONTRACT" && (
                    <>
                      <Field label={t("officer.contractNo")} required>
                        <Input value={form.contract_no} onChange={(e) => set("contract_no", e.target.value)} placeholder="CPCL/CON/2026-27/014" />
                      </Field>
                      <Field label={t("officer.poNo")}>
                        <Input value={form.po_no} onChange={(e) => set("po_no", e.target.value)} placeholder="PO/2026-27/014" />
                      </Field>
                    </>
                  )}
                  {cur.key === "EXECUTION" && (
                    <div className="space-y-2">
                      <span className="label">{t("officer.milestones")} (name | due date, one per line)</span>
                      {milestones.map((m, i) => (
                        <Input
                          key={i}
                          value={m}
                          placeholder="Foundation complete | 2026-11-01"
                          onChange={(e) => setMilestones((ms) => ms.map((x, j) => (j === i ? e.target.value : x)))}
                        />
                      ))}
                      <Button size="sm" variant="ghost" onClick={() => setMilestones((m) => [...m, ""])}>+ {t("common.add")}</Button>
                    </div>
                  )}
                  {cur.key === "DELIVERY" && (
                    <Field label={t("officer.batchSerial")}>
                      <Input value={form.batch_serial} onChange={(e) => set("batch_serial", e.target.value)} placeholder="Batch 1 · SN 4500-4512" />
                    </Field>
                  )}
                  {cur.key === "PAYMENT" && (
                    <>
                      <Field label={t("officer.invoiceNo")}>
                        <Input value={form.invoice_no} onChange={(e) => set("invoice_no", e.target.value)} placeholder="INV/2026/014" />
                      </Field>
                      <Field label={t("officer.paymentRef")} required>
                        <Input value={form.payment_ref} onChange={(e) => set("payment_ref", e.target.value)} placeholder="NEFT/CPCL/88231" />
                      </Field>
                      <Field label={t("officer.paymentAmount")}>
                        <Input type="number" min={0} value={form.payment_amount} onChange={(e) => set("payment_amount", e.target.value)} />
                      </Field>
                    </>
                  )}
                  <Field label={t("common.remarks")} required hint={t("officer.remarksHint")}>
                    <Textarea required value={form.remarks} onChange={(e) => set("remarks", e.target.value)} rows={3} />
                  </Field>
                  <Button className="w-full" disabled={!actionOk || busy} loading={busy} onClick={submit}>
                    <CheckCircle2 size={15} /> {t("officer.completeStage")}
                  </Button>
                  <p className="text-[11px] leading-snug text-slate-500">{t("officer.stageCertNote")}</p>
                </>
              ) : (
                <Alert variant="success">{t("officer.pipelineDoneText")}</Alert>
              )}
            </CardContent>
          </Card>

          {d.certificates.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>{t("officer.certificates")}</CardTitle>
                <CardDescription>{t("officer.certificatesHint")}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {d.certificates.map((c) => (
                  <div key={c.stage} className="flex items-center justify-between rounded-md border border-slate-200 p-2">
                    <div>
                      <div className="text-sm font-medium text-slate-800">{c.label}</div>
                      <div className="font-mono text-[11px] text-slate-500">{c.no}</div>
                    </div>
                    <Button size="sm" variant="outline" onClick={() => downloadCert(c.stage, c.no)}>
                      <Download size={13} /> PDF
                    </Button>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}
        </div>
      </div>

      <OverrideModal
        prefix="/officer"
        applicationId={params.id}
        challenge={challenge}
        onClose={() => setChallenge(null)}
        onDone={(m) => {
          setChallenge(null);
          setMsg({ ok: true, text: m });
          load();
        }}
      />
    </div>
  );
}
