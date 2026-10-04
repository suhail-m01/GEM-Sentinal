"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { AlertTriangle, ClipboardList, ShieldAlert, Users } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatCard, Loading } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

interface Dash {
  applications_total: number;
  by_status: Record<string, number>;
  users_total: number;
  officers_pending_approval: number;
  tenders_published: number;
  overrides_total: number;
  high_risk_overrides: number;
  blacklist_override_queue: number;
  pending_admin_cosigns: number;
  pending_override_reviews: number;
  vigilance_referrals_total: number;
  vigilance_by_status: Record<string, number>;
  sla_breaches: number;
  critical_watchlist: Array<{
    override_id: string;
    application_no: string;
    severity: string;
    officer: string | null;
    status: string;
    cosign_required: boolean;
    cosigned: boolean;
    vigilance_referral_id: string | null;
    at: string;
  }>;
}

export default function AdminDashboard() {
  const t = useTranslations();
  const [d, setD] = useState<Dash | null>(null);

  useEffect(() => {
    api<Dash>(`/admin/dashboard`).then(setD).catch(() => {});
  }, []);

  if (!d) return <Loading />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("admin.dashboard")}</h1>
        <p className="text-sm text-slate-500">{t("common.decisionSupport")}</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
        <StatCard label={t("admin.applicationsTotal")} value={d.applications_total} icon={<ClipboardList size={20} />} />
        <StatCard label={t("admin.usersTotal")} value={d.users_total} icon={<Users size={20} />} />
        <StatCard label={t("admin.officersPending")} value={d.officers_pending_approval} tone={d.officers_pending_approval > 0 ? "warning" : "default"} />
        <StatCard label={t("admin.tendersPublished")} value={d.tenders_published} />
        <StatCard label={t("admin.overridesTotal")} value={d.overrides_total} tone={d.overrides_total > 0 ? "warning" : "default"} sub={`${d.high_risk_overrides} ${t("admin.highRiskOverrides").toLowerCase()}`} />
        <StatCard label={t("admin.referralsTotal")} value={d.vigilance_referrals_total} tone="info" />
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label={t("admin.pendingCosigns")} value={d.pending_admin_cosigns} tone={d.pending_admin_cosigns > 0 ? "critical" : "default"} />
        <StatCard label="Pending override reviews" value={d.pending_override_reviews} tone={d.pending_override_reviews > 0 ? "warning" : "default"} />
        <StatCard label="SLA breaches" value={d.sla_breaches} tone={d.sla_breaches > 0 ? "danger" : "default"} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="flex items-center gap-2"><AlertTriangle size={16} /> {t("admin.criticalWatchlist")}</CardTitle>
            <Link href="/admin/overrides" className="text-sm font-medium text-navy-700 hover:underline">{t("common.view")} →</Link>
          </CardHeader>
          <CardContent>
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-2 pr-3">Application</th>
                  <th className="py-2 pr-3">Severity</th>
                  <th className="py-2 pr-3">Officer</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2">At</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {d.critical_watchlist.map((o) => (
                  <tr key={o.override_id}>
                    <td className="py-2.5 pr-3 font-medium text-navy-900">{o.application_no}</td>
                    <td className="py-2.5 pr-3"><Badge variant="critical">{o.severity}</Badge></td>
                    <td className="py-2.5 pr-3 text-slate-600">{o.officer}</td>
                    <td className="py-2.5 pr-3">
                      <Badge variant={o.status === "PENDING_COSIGN" ? "critical" : o.status === "REJECTED" ? "danger" : "success"}>{o.status.replace(/_/g, " ")}</Badge>
                      {o.cosign_required && !o.cosigned && <span className="ml-1 text-xs text-critical-700">co-sign pending</span>}
                    </td>
                    <td className="py-2.5 text-xs text-slate-500">{fmtDateTime(o.at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {d.critical_watchlist.length === 0 && <p className="py-4 text-sm text-slate-500">{t("common.noData")}</p>}
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader><CardTitle>Applications by status</CardTitle></CardHeader>
            <CardContent className="space-y-1.5">
              {Object.entries(d.by_status).map(([s, n]) => (
                <div key={s} className="flex items-center justify-between text-sm">
                  <span className="text-slate-600">{s}</span>
                  <span className="font-semibold text-navy-900">{n}</span>
                </div>
              ))}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>{t("admin.referralsTitle")}</CardTitle></CardHeader>
            <CardContent className="space-y-1.5">
              {Object.entries(d.vigilance_by_status).map(([s, n]) => (
                <div key={s} className="flex items-center justify-between text-sm">
                  <span className="text-slate-600">{s.replace(/_/g, " ")}</span>
                  <span className="font-semibold text-navy-900">{n}</span>
                </div>
              ))}
              {Object.keys(d.vigilance_by_status).length === 0 && <p className="text-sm text-slate-500">{t("common.noData")}</p>}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
