"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Activity, AlertTriangle, BadgeCheck, ShieldQuestion, Waves } from "lucide-react";
import { get } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Loading } from "@/components/app/widgets";
import { fmtDate, fmtDateTime } from "@/lib/utils";

interface HealthItem {
  attribute: string;
  label: string;
  status: string; // OK | LAPSED | UNVERIFIED
  value: string | null;
  last_checked: string | null;
}
interface ExpiringDoc {
  name: string;
  doc_type: string;
  expiry_date: string;
  days_remaining: number;
}
interface Health {
  items: HealthItem[];
  expiring_documents: ExpiringDoc[];
  score_at_verification: number | null;
  risk_now: string | null;
  last_verified_at: string | null;
  watch_active: boolean;
  recent_drifts: { attribute: string; to: string | null; severity: string; detected_at: string | null }[];
  advice: string | null;
}

const ITEM_STYLE: Record<string, string> = {
  OK: "border-emerald-200 bg-emerald-50",
  LAPSED: "border-red-200 bg-red-50",
  UNVERIFIED: "border-amber-200 bg-amber-50",
};

export default function ComplianceHealthPage() {
  const t = useTranslations("bidder.complianceHealth");
  const [data, setData] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<Health>("/bidder/compliance-health").then(setData).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div>;
  if (!data) return <Loading />;

  const okCount = data.items.filter((i) => i.status === "OK").length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold text-navy-900">
          <ShieldQuestion size={20} className="text-navy-600" /> {t("title")}
        </h1>
        <p className="text-sm text-slate-500">{t("subtitle")}</p>
      </div>

      {data.advice && (
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {data.advice}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-3">
        <Card className="p-4">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <Activity size={13} /> {t("healthSummary")}
          </div>
          <div className="mt-1 text-2xl font-bold text-navy-900">{okCount}/{data.items.length} {t("healthy")}</div>
        </Card>
        <Card className="p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("scoreAtVerification")}</div>
          <div className="mt-1 text-2xl font-bold text-navy-900">{data.score_at_verification ?? "-"}</div>
          {data.last_verified_at && <div className="text-[11px] text-slate-400">{fmtDateTime(data.last_verified_at)}</div>}
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            <Waves size={13} /> {t("continuousWatch")}
          </div>
          <div className={`mt-1 text-sm font-bold ${data.watch_active ? "text-emerald-700" : "text-slate-400"}`}>
            {data.watch_active ? t("watchActive") : t("watchInactive")}
          </div>
        </Card>
      </div>

      <Card className="p-4">
        <h2 className="mb-3 text-sm font-bold text-navy-900">{t("sourceItems")}</h2>
        <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
          {data.items.map((i) => (
            <div key={i.attribute} className={`rounded-md border p-3 ${ITEM_STYLE[i.status] ?? "border-slate-200"}`}>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-navy-900">{i.label}</span>
                {i.status === "OK" ? (
                  <span className="inline-flex items-center gap-1 text-xs font-bold text-emerald-700"><BadgeCheck size={14} /> {t("ok")}</span>
                ) : i.status === "LAPSED" ? (
                  <span className="inline-flex items-center gap-1 text-xs font-bold text-red-700"><AlertTriangle size={14} /> {t("lapsed")}</span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-xs font-bold text-amber-700">? {t("unverified")}</span>
                )}
              </div>
              <div className="mt-1 text-xs text-slate-600">
                {i.value ?? "-"}
                {i.last_checked && <span className="text-slate-400"> · {t("lastChecked")} {fmtDate(i.last_checked)}</span>}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-4">
        <h2 className="mb-3 text-sm font-bold text-navy-900">{t("expiringDocs")}</h2>
        {data.expiring_documents.length === 0 ? (
          <p className="text-sm text-slate-500">{t("noneExpiring")}</p>
        ) : (
          <ul className="space-y-1">
            {data.expiring_documents.map((d, idx) => (
              <li key={idx} className="flex items-center justify-between rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm">
                <span>{d.name} <span className="text-xs text-slate-500">({d.doc_type})</span></span>
                <span className={`font-semibold ${d.days_remaining <= 10 ? "text-red-600" : "text-amber-700"}`}>
                  {d.days_remaining} {t("daysLeft")} · {fmtDate(d.expiry_date)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {data.recent_drifts.length > 0 && (
        <Card className="p-4">
          <h2 className="mb-3 text-sm font-bold text-navy-900">{t("recentChanges")}</h2>
          <ul className="space-y-1">
            {data.recent_drifts.map((d, idx) => (
              <li key={idx} className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2 text-sm">
                <span>
                  <b>{d.attribute}</b> → {d.to ?? "-"}
                  <span className="ml-2 text-xs font-bold text-slate-500">{d.severity}</span>
                </span>
                <span className="text-xs text-slate-400">{d.detected_at ? fmtDateTime(d.detected_at) : ""}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="text-right">
        <Link href="/bidder/applications" className="text-sm font-medium text-navy-700 hover:underline">
          {t("viewApplications")} →
        </Link>
      </div>
    </div>
  );
}
