"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { AlertTriangle, ClipboardList, FileSearch, Hourglass, ShieldAlert } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatCard, AppStatusBadge, RiskBadge, Loading } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

interface Dash {
  total_assigned: number;
  pending_reviews: number;
  ai_verification_not_run: number;
  ai_in_progress: number;
  high_risk: number;
  blacklisted_bidders: number;
  clarification_requests: number;
  sla_breaches: number;
  awaiting_l2: number;
  recent_override_alerts: Array<{ override_id: string; application_no: string; severity: string; officer: string | null; at: string }>;
  applications: Array<{
    id: string;
    application_no: string;
    company: string | null;
    tender_no: string;
    tender_title?: string;
    district?: string;
    status: string;
    risk_level: string | null;
    compliance_score: number | null;
    fraud_flagged?: boolean;
    report_stale?: boolean;
    sla_breached?: boolean;
    submitted_at?: string | null;
  }>;
}

export default function OfficerDashboard() {
  const t = useTranslations();
  const [d, setD] = useState<Dash | null>(null);

  useEffect(() => {
    api<Dash>(`/officer/dashboard`).then(setD).catch(() => {});
  }, []);

  if (!d) return <Loading />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("officer.dashboard")}</h1>
        <p className="text-sm text-slate-500">{t("common.decisionSupport")}</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard label={t("officer.totalAssigned")} value={d.total_assigned} icon={<ClipboardList size={20} />} />
        <StatCard label={t("officer.pendingReviews")} value={d.pending_reviews} tone="info" icon={<ClipboardList size={20} />} />
        <StatCard label={t("officer.aiNotRun")} value={d.ai_verification_not_run} tone="warning" icon={<FileSearch size={20} />} />
        <StatCard label={t("officer.highRisk")} value={d.high_risk} tone={d.high_risk > 0 ? "danger" : "default"} icon={<ShieldAlert size={20} />} sub={`${d.blacklisted_bidders} ${t("officer.blacklisted").toLowerCase()}`} />
        <StatCard label={t("officer.slaBreaches")} value={d.sla_breaches} tone={d.sla_breaches > 0 ? "critical" : "default"} icon={<Hourglass size={20} />} sub={`${d.awaiting_l2} ${t("officer.awaitingL2").toLowerCase()}`} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>{t("nav.queue")}</CardTitle>
            <Link href="/officer/applications" className="text-sm font-medium text-navy-700 hover:underline">{t("common.view")} →</Link>
          </CardHeader>
          <CardContent>
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-2 pr-3">App</th>
                  <th className="py-2 pr-3">Bidder</th>
                  <th className="py-2 pr-3">{t("common.status")}</th>
                  <th className="py-2 pr-3">{t("common.risk")}</th>
                  <th className="py-2">{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {d.applications.slice(0, 8).map((a) => (
                  <tr key={a.id}>
                    <td className="py-2.5 pr-3 font-medium text-navy-900">
                      {a.application_no}
                      {a.sla_breached && <span title="SLA breached" className="ml-1 text-red-600"></span>}
                      {a.report_stale && <span title="Report stale" className="ml-1 text-amber-600">⟳</span>}
                    </td>
                    <td className="py-2.5 pr-3 text-slate-600">{a.company ?? "-"}</td>
                    <td className="py-2.5 pr-3"><AppStatusBadge status={a.status} /></td>
                    <td className="py-2.5 pr-3"><RiskBadge risk={a.risk_level} /></td>
                    <td className="py-2.5">
                      <Link href={`/officer/review/${a.id}`} className="text-sm font-medium text-navy-700 hover:underline">{t("common.view")} →</Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {d.applications.length === 0 && <p className="py-4 text-sm text-slate-500">{t("common.noData")}</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><AlertTriangle size={16} /> {t("officer.overrideAlerts")}</CardTitle>
          </CardHeader>
          <CardContent className="divide-y divide-slate-100">
            {d.recent_override_alerts.map((o) => (
              <div key={o.override_id} className="py-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-800">{o.application_no}</span>
                  <span className="rounded bg-critical-600 px-1.5 py-0.5 text-[10px] font-bold text-white">{o.severity}</span>
                </div>
                <div className="text-xs text-slate-500">
                  {o.officer} · {fmtDateTime(o.at)}
                </div>
              </div>
            ))}
            {d.recent_override_alerts.length === 0 && <p className="py-4 text-sm text-slate-500">{t("common.noData")}</p>}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
