"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge, riskVariant } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Select, Textarea } from "@/components/ui/fields";
import { Table, TBody, TH, TD, THead, TR } from "@/components/ui/table";
import { Loading } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

interface OverrideRow {
  id: string;
  application_no: string;
  tender_no: string;
  district: string;
  officer: string | null;
  officer_email: string | null;
  level: string;
  severity: string;
  status: string;
  reason_category: string;
  finding: Record<string, unknown> | null;
  justification: string;
  admin_cosign_required: boolean;
  admin_cosigned_by: string | null;
  admin_cosigned_at: string | null;
  vigilance_referral_id: string | null;
  review_note: string | null;
  created_at: string;
}

export default function AdminOverridesPage() {
  const t = useTranslations();
  const [rows, setRows] = useState<OverrideRow[] | null>(null);
  const [freq, setFreq] = useState<Record<string, number>>({});
  const [severity, setSeverity] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [review, setReview] = useState<{ row: OverrideRow; action: string } | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    api<{ overrides: OverrideRow[]; officer_frequency: Record<string, number> }>(
      `/admin/overrides${severity ? `?severity=${severity}` : ""}`
    ).then((d) => {
      setRows(d.overrides);
      setFreq(d.officer_frequency);
    });
  useEffect(() => {
    load();
  }, [severity]);

  const reviewSubmit = async () => {
    if (!review) return;
    setBusy(true);
    try {
      const r = await api<{ message: string }>(`/admin/overrides/${review.row.id}/review`, {
        method: "POST",
        body: JSON.stringify({ action: review.action, review_note: note || undefined }),
      });
      setMsg({ ok: true, text: r.message });
      setReview(null);
      setNote("");
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail ?? String(e) });
    } finally {
      setBusy(false);
    }
  };

  const cosign = async (id: string) => {
    setMsg(null);
    try {
      const r = await api<{ message: string }>(`/admin/overrides/${id}/cosign`, { method: "POST" });
      setMsg({ ok: true, text: r.message });
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail ?? String(e) });
    }
  };

  if (!rows) return <Loading />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("admin.overridesTitle")}</h1>
        <p className="text-sm text-slate-500">Every override is recorded with finding, justification, re-auth and hash-chained audit.</p>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

      <div className="flex items-center gap-3">
        <Card className="w-64 p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("admin.officerFrequency")}</div>
          <div className="mt-1 space-y-0.5">
            {Object.entries(freq).map(([k, v]) => (
              <div key={k} className="flex justify-between text-xs">
                <span className="truncate pr-2 text-slate-600">{k}</span>
                <b>{v}</b>
              </div>
            ))}
            {Object.keys(freq).length === 0 && <p className="text-xs text-slate-400">-</p>}
          </div>
        </Card>
        <Select className="max-w-48" value={severity} onChange={(e) => setSeverity(e.target.value)}>
          <option value="">Severity: All</option>
          {["MINOR", "MEDIUM", "HIGH", "CRITICAL", "FRAUD"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
      </div>

      <Table>
        <THead>
          <TR>
            <TH>Application</TH>
            <TH>Officer</TH>
            <TH>Severity</TH>
            <TH>{t("common.status")}</TH>
            <TH>{t("admin.reasonCategory")}</TH>
            <TH>Co-sign</TH>
            <TH>Vigilance</TH>
            <TH>{t("common.date")}</TH>
            <TH>{t("common.actions")}</TH>
          </TR>
        </THead>
        <TBody>
          {rows.map((o) => (
            <TR key={o.id}>
              <TD>
                <Link href={`/officer/review/${o.application_no}`} className="font-medium text-navy-900 hover:underline">
                  {o.application_no}
                </Link>
                <div className="text-xs text-slate-500">{o.tender_no} · {o.district}</div>
              </TD>
              <TD className="text-xs">
                <div>{o.officer}</div>
                <div className="text-slate-500">{o.officer_email} · {o.level}</div>
              </TD>
              <TD><Badge variant={riskVariant(o.severity)}>{o.severity}</Badge></TD>
              <TD>
                <Badge variant={o.status === "PENDING_COSIGN" ? "critical" : o.status === "REJECTED" ? "danger" : o.status === "ESCALATED" ? "warning" : "success"}>
                  {o.status.replace(/_/g, " ")}
                </Badge>
              </TD>
              <TD>
                <div className="max-w-[180px]">
                  <div className="text-xs font-medium">{o.reason_category}</div>
                  <div className="truncate text-[11px] text-slate-500" title={o.justification}>{o.justification}</div>
                </div>
              </TD>
              <TD>
                {!o.admin_cosign_required ? (
                  <span className="text-xs text-slate-400">n/a</span>
                ) : o.admin_cosigned_at ? (
                  <span className="text-xs text-emerald-700">✓ {o.admin_cosigned_by}</span>
                ) : (
                  <Button size="sm" variant="critical" onClick={() => cosign(o.id)}>
                    {t("admin.cosign")}
                  </Button>
                )}
              </TD>
              <TD>
                {o.vigilance_referral_id ? (
                  <Link href="/admin/referrals" className="text-xs font-medium text-purple-700 hover:underline">
                    {o.vigilance_referral_id}
                  </Link>
                ) : (
                  <span className="text-xs text-slate-400">-</span>
                )}
              </TD>
              <TD className="whitespace-nowrap text-xs">{fmtDateTime(o.created_at)}</TD>
              <TD>
                <div className="flex gap-1">
                  <Button size="sm" variant="outline" onClick={() => { setReview({ row: o, action: "mark_reviewed" }); setNote(o.review_note ?? ""); }}>
                    {t("admin.markReviewed")}
                  </Button>
                  <Button size="sm" variant="destructive" onClick={() => { setReview({ row: o, action: "reject" }); setNote(""); }}>
                    {t("admin.rejectOverride")}
                  </Button>
                </div>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {rows.length === 0 && <p className="text-sm text-slate-500">{t("common.noData")}</p>}

      <Dialog
        open={!!review}
        onClose={() => setReview(null)}
        title={`${t("admin.rejectOverride")} / ${t("admin.markReviewed")} - ${review?.row.application_no ?? ""}`}
      >
        <div className="space-y-4">
          <Alert variant={review?.action === "reject" ? "danger" : "info"}>
            {review?.action === "reject"
              ? "Rejecting an override returns the application to L1 for re-decision and notifies the officer."
              : "Marks the override as reviewed with your note."}
          </Alert>
          <Field label={t("common.remarks")}>
            <Textarea value={note} onChange={(e) => setNote(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setReview(null)}>{t("common.cancel")}</Button>
            <Button variant={review?.action === "reject" ? "destructive" : "default"} loading={busy} onClick={reviewSubmit}>
              {t("common.submit")}
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
