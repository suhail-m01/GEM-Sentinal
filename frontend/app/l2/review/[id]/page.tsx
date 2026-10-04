"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { CheckCircle2, Lock, Send } from "lucide-react";
import { api, downloadFile } from "@/lib/api";
import type { OverrideChallenge } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/fields";
import { Tabs } from "@/components/ui/tabs";
import { Loading, AppStatusBadge, RiskBadge, EligibilityBadge } from "@/components/app/widgets";
import { OverrideModal } from "@/components/app/override-modal";
import { ReportTab } from "@/components/app/report-tab";
import { fmtDate, fmtDateTime } from "@/lib/utils";

interface Review {
  application: any;
  bidder: any;
  tender: any;
  documents: any[];
  verification_report: any;
  last_verification: string | null;
  clarifications: any[];
  decisions: any[];
  overrides: any[];
  previous_applications: any[];
  timeline: any[];
  audit_trail: any[];
  report_stale: boolean;
  l1_recommendation: {
    action: string;
    remarks: string | null;
    is_overridden: boolean;
    officer: string | null;
    at: string;
  } | null;
  admin_cosign_pending: boolean;
  admin_cosign_required: boolean;
}

export default function L2ReviewPage() {
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
  const [tab, setTab] = useState("report");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [challenge, setChallenge] = useState<OverrideChallenge | null>(null);
  const [remarksOpen, setRemarksOpen] = useState(false);
  const [remarks, setRemarks] = useState("");
  const [clarOpen, setClarOpen] = useState(false);
  const [clarReason, setClarReason] = useState("");
  const [clarDocs, setClarDocs] = useState("");
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Review>(`/l2/applications/${params.id}`)
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
                <span className="font-medium text-navy-800">{fmtDateTime(sealed.bid_deadline_ist)} (IST)</span>
              </div>
            </div>
            <p className="text-xs text-slate-500">{t("officer.sealedNote")}</p>
            <Link href="/l2" className="mt-1 text-sm text-navy-700 hover:underline">
              ← {t("nav.l2Queue")}
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (err) return <Alert variant="danger">{err}</Alert>;
  if (!d) return <Loading />;

  const app = d.application;
  const canAct = !["Approved", "Rejected", "Withdrawn", "Closed"].includes(app.status);
  const actionNeedsRemarks = (a: string) => a === "REJECT" || a === "APPROVE_WITH_OBSERVATION" || a === "SEND_BACK_TO_L1" || a === "ESCALATE_TO_ADMIN";

  const decide = async (action: string) => {
    setMsg(null);
    let finalRemarks = remarks || undefined;
    try {
      const r = await api(`/l2/applications/${params.id}/decision`, {
        method: "POST",
        body: JSON.stringify({
          action,
          remarks: finalRemarks,
          clarification_reason: action === "ADDITIONAL_CLARIFICATION" ? clarReason : undefined,
          clarification_documents: action === "ADDITIONAL_CLARIFICATION" ? clarDocs.split(",").map((s) => s.trim()).filter(Boolean) : [],
        }),
      });
      setMsg({ ok: true, text: (r as any)?.message ?? `Decision recorded: ${action}` });
      setRemarksOpen(false);
      setClarOpen(false);
      setRemarks("");
      load();
    } catch (e2: any) {
      const det = e2?.detail;
      if (e2?.status === 409 && typeof det === "object" && det && (det as any).override_required) {
        setChallenge(det as OverrideChallenge);
      } else {
        // 409 can also be the Sentinel Watch gate (WATCH_GATE_BLOCKED) or a
        // co-sign block - show the server message instead of the override modal.
        setMsg({ ok: false, text: typeof det === "string" ? det : det?.message ?? JSON.stringify(det) ?? String(e2) });
      }
    }
  };

  const click = (action: string) => {
    if (action === "ADDITIONAL_CLARIFICATION") {
      setClarOpen(true);
      return;
    }
    if (actionNeedsRemarks(action) && !remarks.trim()) {
      setPendingAction(action);
      setRemarksOpen(true);
      return;
    }
    decide(action);
  };

  const confirmRemarks = async () => {
    const action = pendingAction;
    setRemarksOpen(false);
    setPendingAction(null);
    if (action) await decide(action);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/l2" className="text-sm text-navy-700 hover:underline">← {t("nav.l2Queue")}</Link>
          <h1 className="mt-1 text-xl font-bold text-navy-900">{app.application_no}</h1>
          <p className="text-sm text-slate-500">
            {app.company} · {d.tender.tender_no} ({d.tender.title})
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <AppStatusBadge status={app.status} />
            <RiskBadge risk={app.risk_level} />
            <EligibilityBadge value={app.eligibility_result ?? ""} />
            {d.admin_cosign_pending && <Badge variant="critical">Admin co-sign pending</Badge>}
          </div>
        </div>
        {d.l1_recommendation && (
          <Card className="max-w-sm p-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">L1 recommendation</div>
            <div className="mt-1 text-sm font-medium text-slate-800">
              {d.l1_recommendation.action.replace(/_/g, " ").toLowerCase()}
            </div>
            {d.l1_recommendation.remarks && <div className="mt-0.5 text-xs text-slate-600">“{d.l1_recommendation.remarks}”</div>}
            <div className="mt-1 text-[11px] text-slate-400">
              {d.l1_recommendation.officer} · {fmtDateTime(d.l1_recommendation.at)}
            </div>
          </Card>
        )}
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}
      {d.admin_cosign_pending && (
        <Alert variant="critical" title="Admin co-sign pending">
          A critical override on this application requires Admin co-signature before final approval. Approval is blocked until it is complete.
        </Alert>
      )}

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Tabs
            tabs={[
              { id: "report", label: t("officer.reportTitle") },
              { id: "overview", label: t("officer.bidderProfile") },
              { id: "decisions", label: t("common.audit") },
            ]}
            active={tab}
            onChange={setTab}
          />

          {tab === "report" && (
            <ReportTab
              rep={d.verification_report}
              onDownload={async () => {
                setMsg(null);
                try {
                  await downloadFile(`/l2/applications/${params.id}/report/download`, `report-${app.application_no}.pdf`);
                } catch (e2: any) {
                  const det = e2?.detail;
                  setMsg({ ok: false, text: typeof det === "string" ? det : det?.message ?? JSON.stringify(det) ?? String(e2) });
                }
              }}
            />
          )}
          {tab === "overview" && (
            <Card>
              <CardHeader><CardTitle>{t("officer.bidderProfile")}</CardTitle></CardHeader>
              <CardContent className="grid gap-3 text-sm sm:grid-cols-2">
                <div><span className="label">Name</span><div>{d.bidder.name} ({d.bidder.company})</div></div>
                <div><span className="label">{t("common.district")}</span><div>{d.bidder.district} ({d.bidder.state})</div></div>
                <div><span className="label">PAN</span><div className="font-mono">{d.bidder.pan_masked ?? "-"}</div></div>
                <div><span className="label">GSTIN</span><div className="font-mono">{d.bidder.gstin_masked ?? "-"}</div></div>
                <div><span className="label">{t("common.category")}</span><div>{d.tender.category ?? "-"}</div></div>
                <div><span className="label">Close date</span><div>{fmtDate(d.tender.close_date)}</div></div>
              </CardContent>
            </Card>
          )}
          {tab === "decisions" && (
            <div className="space-y-4">
              <Card>
                <CardHeader><CardTitle>Officer decisions</CardTitle></CardHeader>
                <CardContent>
                  <div className="divide-y divide-slate-100">
                    {d.decisions.map((x) => (
                      <div key={x.id} className="flex items-center justify-between py-2.5">
                        <div>
                          <span className="text-sm font-medium text-slate-800">{x.action}</span>
                          <span className="ml-2 text-xs text-slate-500">{x.level} · {x.officer} · {fmtDateTime(x.at)}</span>
                          {x.is_overridden && <Badge variant="warning" className="ml-2">OVERRIDDEN</Badge>}
                        </div>
                        {x.remarks && <span className="max-w-[50%] truncate text-xs text-slate-500">{x.remarks}</span>}
                      </div>
                    ))}
                    {d.decisions.length === 0 && <p className="text-sm text-slate-500">{t("common.noData")}</p>}
                  </div>
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle>Overrides</CardTitle></CardHeader>
                <CardContent>
                  {d.overrides.map((o) => (
                    <div key={o.id} className="mb-3 rounded-md border border-slate-200 p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="critical">{o.severity}</Badge>
                        <Badge variant={o.status === "PENDING_COSIGN" ? "warning" : "success"}>{o.status}</Badge>
                        <span className="text-xs text-slate-500">{o.officer} · {fmtDateTime(o.at)}</span>
                      </div>
                      <p className="mt-1 text-xs text-slate-700"><b>{o.reason_category}:</b> {o.justification}</p>
                    </div>
                  ))}
                  {d.overrides.length === 0 && <p className="text-sm text-slate-500">{t("common.noData")}</p>}
                </CardContent>
              </Card>
            </div>
          )}
        </div>

        <div>
          <Card className="sticky top-20">
            <CardHeader>
              <CardTitle>{t("officer.decisionPanel")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <Button className="w-full" disabled={!canAct} onClick={() => click("APPROVE")}>
                <CheckCircle2 size={15} /> {t("officer.l2Approve")}
              </Button>
              <Button variant="secondary" className="w-full" disabled={!canAct} onClick={() => click("APPROVE_WITH_OBSERVATION")}>
                {t("officer.l2ApproveObservation")}
              </Button>
              <Button variant="destructive" className="w-full" disabled={!canAct} onClick={() => click("REJECT")}>
                {t("officer.l2Reject")}
              </Button>
              <Button variant="outline" className="w-full" disabled={!canAct} onClick={() => click("SEND_BACK_TO_L1")}>
                {t("officer.l2SendBack")}
              </Button>
              <Button variant="warning" className="w-full" disabled={!canAct} onClick={() => click("ADDITIONAL_CLARIFICATION")}>
                {t("officer.l2AdditionalClarification")}
              </Button>
              <Button variant="outline" className="w-full" disabled={!canAct} onClick={() => click("REQUIRE_FRESH_VERIFICATION")}>
                {t("officer.l2FreshVerification")}
              </Button>
              <Button variant="critical" className="w-full" disabled={!canAct} onClick={() => click("ESCALATE_TO_ADMIN")}>
                <Send size={14} /> {t("officer.l2Escalate")}
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>

      <Dialog open={remarksOpen} onClose={() => setRemarksOpen(false)} title={t("common.remarks")}>
        <div className="space-y-4">
          <Field label={t("common.remarks")} required>
            <Textarea required value={remarks} onChange={(e) => setRemarks(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => { setRemarksOpen(false); setPendingAction(null); }}>{t("common.cancel")}</Button>
            <Button onClick={confirmRemarks} disabled={!remarks.trim()}>{t("common.submit")}</Button>
          </div>
        </div>
      </Dialog>

      <Dialog open={clarOpen} onClose={() => setClarOpen(false)} title={t("officer.l2AdditionalClarification")}>
        <div className="space-y-4">
          <Field label={t("officer.clarificationReason")} required>
            <Textarea required value={clarReason} onChange={(e) => setClarReason(e.target.value)} />
          </Field>
          <Field label={t("officer.documentsNeeded")}>
            <Input value={clarDocs} onChange={(e) => setClarDocs(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setClarOpen(false)}>{t("common.cancel")}</Button>
            <Button variant="warning" onClick={() => decide("ADDITIONAL_CLARIFICATION")}>{t("common.submit")}</Button>
          </div>
        </div>
      </Dialog>

      <OverrideModal
        prefix="/l2"
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
