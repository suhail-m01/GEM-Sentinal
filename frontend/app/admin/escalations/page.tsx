"use client";

import React, { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Select, Textarea } from "@/components/ui/fields";
import { Loading } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

interface Grievance {
  id: string;
  application_no: string;
  bidder: string;
  reason: string;
  details: string | null;
  status: string;
  admin_response: string | null;
  created_at: string;
}

export default function EscalationsPage() {
  const t = useTranslations();
  const [grievances, setGrievances] = useState<Grievance[] | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [resp, setResp] = useState<Grievance | null>(null);
  const [outcome, setOutcome] = useState("RESPONDED");
  const [response, setResponse] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api<{ grievances: Grievance[] }>(`/admin/grievances`).then((d) => setGrievances(d.grievances));
  useEffect(() => {
    load();
  }, []);

  const submit = async () => {
    if (!resp) return;
    setBusy(true);
    try {
      const r = await api<{ message: string }>(`/admin/grievances/${resp.id}/respond`, {
        method: "POST",
        body: JSON.stringify({ outcome, response }),
      });
      setMsg({ ok: true, text: r.message });
      setResp(null);
      setResponse("");
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail ?? String(e) });
    } finally {
      setBusy(false);
    }
  };

  if (!grievances) return <Loading />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("nav.escalations")}</h1>
        <p className="text-sm text-slate-500">
          Bidder grievances/appeals, fraud-flagged applications and escalated overrides. Critical overrides appear in{" "}
          <a href="/admin/overrides" className="font-medium text-navy-700 underline">Override Monitoring</a>.
        </p>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>{t("nav.grievance")} (appeals against decisions)</CardTitle>
        </CardHeader>
        <CardContent className="divide-y divide-slate-100">
          {grievances.map((g) => (
            <div key={g.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-navy-900">{g.application_no}</span>
                  <Badge variant={g.status === "RESPONDED" ? "info" : g.status === "ALLOWED" ? "success" : g.status === "DISMISSED" ? "danger" : "warning"}>
                    {g.status.replace(/_/g, " ")}
                  </Badge>
                </div>
                <div className="mt-0.5 text-xs text-slate-500">
                  {g.bidder} · {fmtDateTime(g.created_at)}
                </div>
                <div className="mt-1 text-sm text-slate-700">
                  <b>{g.reason}</b>
                  {g.details ? ` - ${g.details}` : ""}
                </div>
                {g.admin_response && (
                  <div className="mt-1 rounded bg-slate-50 p-2 text-xs text-slate-600">
                    <b>Admin response:</b> {g.admin_response}
                  </div>
                )}
              </div>
              {g.status === "OPEN" && (
                <Button size="sm" variant="secondary" onClick={() => { setResp(g); setResponse(""); setOutcome("RESPONDED"); }}>
                  {t("admin.review")}
                </Button>
              )}
            </div>
          ))}
          {grievances.length === 0 && <p className="py-4 text-sm text-slate-500">{t("common.noData")}</p>}
        </CardContent>
      </Card>

      <Dialog open={!!resp} onClose={() => setResp(null)} title={`${t("admin.review")} - ${resp?.application_no ?? ""}`}>
        <div className="space-y-4">
          <Field label="Outcome" required>
            <Select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
              <option value="RESPONDED">RESPONDED</option>
              <option value="ALLOWED">ALLOWED (decision corrected)</option>
              <option value="DISMISSED">DISMISSED</option>
            </Select>
          </Field>
          <Field label={t("common.remarks")} required hint="Min 10 characters">
            <Textarea required minLength={10} value={response} onChange={(e) => setResponse(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setResp(null)}>{t("common.cancel")}</Button>
            <Button loading={busy} onClick={submit} disabled={response.length < 10}>{t("common.submit")}</Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
