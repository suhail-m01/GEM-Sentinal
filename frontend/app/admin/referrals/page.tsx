"use client";

import React, { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Download, Eye } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Textarea } from "@/components/ui/fields";
import { Table, TBody, TH, TD, THead, TR } from "@/components/ui/table";
import { Loading } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

interface Referral {
  id: string;
  referral_no: string;
  application_no: string;
  application_id: string;
  status: string;
  severity: string | null;
  created_at: string;
  reviewed_at: string | null;
  filed_at: string | null;
}

interface Detail {
  referral: {
    id: string;
    referral_no: string;
    status: string;
    report: Record<string, unknown> | null;
    notice_text: string | null;
    created_at: string;
  };
}

export default function ReferralsPage() {
  const t = useTranslations();
  const [rows, setRows] = useState<Referral[] | null>(null);
  const [status, setStatus] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    api<{ referrals: Referral[] }>(`/admin/vigilance-referrals${status ? `?status=${status}` : ""}`).then((d) => setRows(d.referrals));
  useEffect(() => {
    load();
  }, [status]);

  const open = (id: string) =>
    api<Detail>(`/admin/vigilance-referrals/${id}`).then((d) => {
      setDetail(d);
      setNote("");
    });

  const act = async (id: string, path: string) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ message: string }>(`/admin/vigilance-referrals/${id}${path}`, {
        method: "POST",
        body: JSON.stringify({ note: note || undefined }),
      });
      setMsg({ ok: true, text: r.message });
      setDetail(null);
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail ?? String(e) });
    } finally {
      setBusy(false);
    }
  };

  if (!rows) return <Loading />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("admin.referralsTitle")}</h1>
        <p className="text-sm text-slate-500">{t("admin.referralHint")}</p>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

      <Card className="p-4">
        <select className="field max-w-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Status: All</option>
          {["DRAFT", "REVIEWED", "READY_FOR_CVC_FILING", "FILED_MANUALLY"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </Card>

      <Table>
        <THead>
          <TR>
            <TH>Referral</TH>
            <TH>Application</TH>
            <TH>Severity</TH>
            <TH>{t("common.status")}</TH>
            <TH>Created</TH>
            <TH>{t("common.actions")}</TH>
          </TR>
        </THead>
        <TBody>
          {rows.map((r) => (
            <TR key={r.id}>
              <TD className="font-medium text-navy-900">{r.referral_no}</TD>
              <TD className="text-slate-600">{r.application_no}</TD>
              <TD>{r.severity ? <Badge variant="critical">{r.severity}</Badge> : "-"}</TD>
              <TD>
                <Badge
                  variant={
                    r.status === "DRAFT"
                      ? "slate"
                      : r.status === "REVIEWED"
                      ? "info"
                      : r.status === "READY_FOR_CVC_FILING"
                      ? "warning"
                      : "success"
                  }
                >
                  {r.status.replace(/_/g, " ")}
                </Badge>
              </TD>
              <TD className="text-xs">{fmtDateTime(r.created_at)}</TD>
              <TD>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm" variant="outline" onClick={() => open(r.id)}>
                    <Eye size={13} /> {t("common.view")}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => window.open(`/api/admin/vigilance-referrals/${r.id}/export`, "_blank")}>
                    <Download size={13} /> {t("admin.exportNotice")}
                  </Button>
                </div>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
      {rows.length === 0 && <p className="text-sm text-slate-500">{t("common.noData")}</p>}

      <Dialog open={!!detail} onClose={() => setDetail(null)} title={`${detail?.referral.referral_no ?? ""} - ${detail?.referral.status ?? ""}`} wide>
        {detail && (
          <div className="space-y-4">
            <div className="max-h-72 overflow-y-auto rounded-md border border-slate-200 p-3">
              <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-slate-700">
                {JSON.stringify(detail.referral.report, null, 2)}
              </pre>
            </div>
            {detail.referral.notice_text && (
              <div>
                <div className="label mb-1">Notice text (for external filing)</div>
                <div className="max-h-56 overflow-y-auto whitespace-pre-line rounded-md bg-slate-50 p-3 text-xs text-slate-700">
                  {detail.referral.notice_text}
                </div>
              </div>
            )}
            <Field label={t("common.remarks")}>
              <Textarea value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
            <div className="flex flex-wrap justify-end gap-2">
              {detail.referral.status === "DRAFT" && (
                <Button variant="secondary" loading={busy} onClick={() => act(detail.referral.id, "/review")}>
                  {t("admin.review")}
                </Button>
              )}
              {(detail.referral.status === "DRAFT" || detail.referral.status === "REVIEWED") && (
                <Button variant="warning" loading={busy} onClick={() => act(detail.referral.id, "/mark-ready")}>
                  {t("admin.markReady")}
                </Button>
              )}
              {detail.referral.status === "READY_FOR_CVC_FILING" && (
                <Button variant="default" loading={busy} onClick={() => act(detail.referral.id, "/filed")}>
                  {t("admin.markFiled")}
                </Button>
              )}
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}
