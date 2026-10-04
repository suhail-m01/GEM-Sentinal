"use client";

import React, { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Loading } from "@/components/app/widgets";
import { StatCard } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

interface Analytics {
  average_verification_days: number | null;
  applications_by_status: Record<string, number>;
  rejection_reasons: Record<string, string>;
  clarification_frequency: number;
  tender_wise_compliance: Record<string, number>;
  district_wise_workload: Record<string, number>;
  portal_failure_rate: Record<string, number>;
  low_confidence_ocr_documents: number;
  override_trends: Record<string, number>;
  officer_override_frequency: Record<string, number>;
  sla_performance: { breaches: number; total: number; breach_rate: number };
  bidder_participation_rate: number;
}

interface SourceHealth {
  sources: Array<{ source: string; status: string; message: string | null; checked_at: string }>;
}

const STATUS_COLORS: Record<string, string> = {
  Approved: "#059669",
  Rejected: "#dc2626",
  Withdrawn: "#94a3b8",
  "Admin Co-sign Required": "#b91c1c",
  "L1 Recommended": "#7c3aed",
  "Pending L2 Approval": "#7c3aed",
  "Clarification Requested": "#d97706",
  "AI Verification Not Run": "#0284c7",
  "AI Verification In Progress": "#0ea5e9",
  "Pending L1 Review": "#2563eb",
  Resubmitted: "#0d9488",
};

export default function AnalyticsPage() {
  const t = useTranslations();
  const [a, setA] = useState<Analytics | null>(null);
  const [health, setHealth] = useState<SourceHealth | null>(null);

  useEffect(() => {
    api<Analytics>(`/admin/analytics`).then(setA).catch(() => {});
    api<SourceHealth>(`/admin/source-health`).then(setHealth).catch(() => {});
  }, []);

  if (!a) return <Loading />;

  const statusData = Object.entries(a.applications_by_status).map(([name, value]) => ({ name, value }));
  const districtData = Object.entries(a.district_wise_workload).map(([name, value]) => ({ name, value }));
  const overrideData = Object.entries(a.override_trends).map(([name, value]) => ({ name, value }));
  const officerData = Object.entries(a.officer_override_frequency).map(([name, value]) => ({ name, value }));
  const tenderData = Object.entries(a.tender_wise_compliance).map(([name, value]) => ({ name, value }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("admin.analyticsTitle")}</h1>
        <p className="text-sm text-slate-500">System-level procurement KPIs</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label={t("admin.avgVerification")} value={a.average_verification_days != null ? `${a.average_verification_days} d` : "-"} />
        <StatCard label={t("admin.clarificationFreq")} value={a.clarification_frequency} />
        <StatCard label="SLA breach rate" value={`${Math.round(a.sla_performance.breach_rate * 100)}%`} sub={`${a.sla_performance.breaches}/${a.sla_performance.total}`} tone={a.sla_performance.breaches > 0 ? "warning" : "success"} />
        <StatCard label={t("admin.participation")} value={`${Math.round(a.bidder_participation_rate * 100)}%`} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>{t("admin.appsByStatus")}</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie data={statusData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} label={(x: any) => `${x.name}: ${x.value}`}>
                  {statusData.map((s) => (
                    <Cell key={s.name} fill={STATUS_COLORS[s.name] ?? "#64748b"} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("admin.districtWorkload")}</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={districtData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="value" name="Applications" fill="#0b2545" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("admin.overrideTrends")} (by month)</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={overrideData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="value" name="Overrides" fill="#b91c1c" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("admin.tenderCompliance")} (avg score)</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={tenderData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis domain={[0, 100]} />
                <Tooltip />
                <Bar dataKey="value" name="Avg score" fill="#0d9488" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("admin.officerFrequency")}</CardTitle></CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={officerData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="value" name="Overrides" fill="#d97706" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>{t("admin.sourceHealth")}</CardTitle></CardHeader>
          <CardContent className="divide-y divide-slate-100">
            {(health?.sources ?? []).map((s) => (
              <div key={s.source} className="flex items-center justify-between py-2">
                <div>
                  <span className="text-sm font-medium text-slate-800">{s.source}</span>
                  <div className="text-[11px] text-slate-500">{s.message ?? ""} · {fmtDateTime(s.checked_at)}</div>
                </div>
                <Badge variant={s.status === "UP" ? "success" : "danger"}>{s.status}</Badge>
              </div>
            ))}
            {(health?.sources ?? []).length === 0 && <p className="py-4 text-sm text-slate-500">-</p>}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("admin.rejectionReasons")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {Object.entries(a.rejection_reasons).map(([k, v]) => (
              <div key={k} className="flex items-center justify-between text-sm">
                <span className="truncate pr-3 text-slate-600">{k}</span>
                <b>{v}</b>
              </div>
            ))}
            {Object.keys(a.rejection_reasons).length === 0 && <p className="text-sm text-slate-500">-</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>{t("admin.portalFailure")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {Object.entries(a.portal_failure_rate).map(([k, v]) => (
              <div key={k} className="flex items-center justify-between text-sm">
                <span className="text-slate-600">{k}</span>
                <b className={v > 0.1 ? "text-red-600" : "text-emerald-600"}>{(v * 100).toFixed(1)}%</b>
              </div>
            ))}
            {Object.keys(a.portal_failure_rate).length === 0 && <p className="text-sm text-slate-500">-</p>}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
