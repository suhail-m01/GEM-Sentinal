"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ShieldCheck, ShieldOff } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Loading } from "@/components/app/widgets";
import { fmtDate } from "@/lib/utils";

const SOURCES: Array<{ id: string; label: string; desc: string }> = [
  { id: "UDYAM", label: "Udyam (MSME)", desc: "MSME/Udyam certification records" },
  { id: "GST", label: "GST Portal", desc: "GST registration & compliance records" },
  { id: "PAN", label: "Income Tax (PAN)", desc: "PAN validation records" },
  { id: "MCA21", label: "MCA21", desc: "Incorporation & annual filing records" },
  { id: "EPFO", label: "EPFO", desc: "EPFO registration & compliance records" },
  { id: "ESIC", label: "ESIC", desc: "ESIC compliance records" },
  { id: "STARTUP_INDIA", label: "Startup India", desc: "Startup recognition records" },
  { id: "NSIC", label: "NSIC", desc: "NSIC registration records" },
  { id: "BIS", label: "BIS", desc: "BIS certification records" },
];

interface ConsentRow {
  source: string;
  status: "GRANTED" | "REVOKED";
  purpose: string | null;
  granted_at: string | null;
  revoked_at: string | null;
}

export default function ConsentPage() {
  const t = useTranslations();
  const [rows, setRows] = useState<ConsentRow[] | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  // Where the user came from (apply dialog links here with return context)
  const [origin, setOrigin] = useState<{ tender: string; returnTo: string } | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const returnTo = q.get("return_to");
    if (returnTo && q.get("from") === "apply") {
      setOrigin({ tender: q.get("tender") || "", returnTo });
    }
  }, []);

  const load = () => api<ConsentRow[]>(`/bidder/consent`).then(setRows).catch(() => setRows([]));
  useEffect(() => {
    load();
  }, []);

  const action = async (sources: string[], revoke: boolean) => {
    setBusy(true);
    setMsg("");
    try {
      await api(
        `/bidder/consent${revoke ? "/revoke" : ""}`,
        { method: "POST", body: JSON.stringify({ sources, purpose: "Compliance verification for tender eligibility" }) }
      );
      setMsg(revoke ? "Consent revoked for selected sources." : "Consent granted. You can now fetch verified documents.");
      await load();
    } catch (err: any) {
      setMsg(err?.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!rows) return <Loading />;
  const statusOf = (s: string) => rows.find((r) => r.source === s)?.status ?? "NOT_SET";

  const backHref = origin ? `${origin.returnTo}?resume=1` : "/bidder/tenders";
  const backLabel = origin
    ? `${t("bidder.backToTender")}${origin.tender ? ` - ${origin.tender}` : ""}`
    : `${t("common.back")} - ${t("nav.tenders")}`;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">{t("bidder.consentTitle")}</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">{t("bidder.consentDesc")}</p>
        </div>
        <Link
          href={backHref}
          className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-300 bg-white px-4 text-sm font-medium text-slate-800 transition-colors hover:bg-slate-50"
        >
          ← {backLabel}
        </Link>
      </div>

      {msg && <Alert variant={msg.startsWith("Consent") ? "success" : "danger"}>{msg}</Alert>}

      {origin && msg.startsWith("Consent granted") && (
        <Alert variant="success" title={t("bidder.grantConsent")}>
          <p>{t("bidder.consentDoneApply")}</p>
          <Link
            href={`${origin.returnTo}?resume=1`}
            className="mt-3 inline-flex h-10 items-center justify-center gap-2 rounded-md bg-navy-900 px-5 text-sm font-medium text-white transition-colors hover:bg-navy-800"
          >
            {t("bidder.returnToSubmit")} →
          </Link>
        </Alert>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Verification sources</CardTitle>
          <Button
            size="sm"
            variant="outline"
            onClick={() => action(rows.every((r) => r.status === "REVOKED") ? SOURCES.map((s) => s.id) : ["ALL"], rows.every((r) => r.status === "REVOKED"))}
          >
            {rows.some((r) => r.status === "GRANTED") ? t("bidder.revokeConsent") : t("bidder.grantConsent")} - {t("bidder.allSources")}
          </Button>
        </CardHeader>
        <CardContent className="divide-y divide-slate-100">
          {SOURCES.map((s) => {
            const granted = statusOf(s.id) === "GRANTED";
            return (
              <div key={s.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="flex items-center gap-3">
                  {granted ? (
                    <ShieldCheck size={18} className="text-emerald-600" />
                  ) : (
                    <ShieldOff size={18} className="text-slate-400" />
                  )}
                  <div>
                    <div className="text-sm font-medium text-slate-900">{s.label}</div>
                    <div className="text-xs text-slate-500">{s.desc}</div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant={granted ? "success" : "slate"}>{granted ? "GRANTED" : statusOf(s.id) === "REVOKED" ? "REVOKED" : "NOT SET"}</Badge>
                  <Button size="sm" variant={granted ? "outline" : "secondary"} loading={busy} onClick={() => action([s.id], granted)}>
                    {granted ? t("bidder.revokeConsent") : t("bidder.grantConsent")}
                  </Button>
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>

      <Alert variant="info">
        {t("common.principle")} - <Link className="font-medium underline" href="/bidder/vault">{t("bidder.fetchDocuments")} →</Link>
      </Alert>
    </div>
  );
}
