"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { CalendarClock, CheckCircle2, ClipboardList, FileCheck2, HelpCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { TenderCard } from "@/lib/types";
import { useAuth } from "@/lib/auth";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, eligibilityVariant, eligibilityLabel } from "@/components/ui/badge";
import { StatCard, AppStatusBadge, Loading } from "@/components/app/widgets";
import { fmtDate, formatINR } from "@/lib/utils";

interface Dash {
  profile: {
    company_name: string; profile_completion: number; district: string | null; state: string | null;
  };
  verified_document_count: number;
  total_document_count: number;
  eligible_tender_count: number;
  open_tender_count: number;
  pending_applications: number;
  clarification_requests: number;
  applications: Array<{
    id: string;
    application_no: string;
    tender: string;
    status: string;
    risk_level: string | null;
    compliance_score: number | null;
    submitted_at: string | null;
  }>;
  reminders: Array<{ tender_no: string; title: string; close_date: string; days_left: number }>;
}

export default function BidderDashboard() {
  const t = useTranslations();
  const { user } = useAuth();
  const [d, setD] = useState<Dash | null>(null);
  const [tenders, setTenders] = useState<TenderCard[]>([]);

  useEffect(() => {
    api<Dash>(`/bidder/dashboard`).then(setD).catch(() => {});
    api<{ tenders: TenderCard[] }>(`/bidder/tenders`).then((r) => setTenders(r.tenders)).catch(() => {});
  }, []);

  if (!d) return <Loading />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">Hello, {user?.full_name.split(" ")[0]}</h1>
          <p className="text-sm text-slate-500">{t("common.tagline")}</p>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard
          label={t("bidder.profileCompletion")}
          value={`${d.profile.profile_completion}%`}
          tone={d.profile.profile_completion >= 80 ? "success" : "warning"}
          sub={d.profile.company_name}
          icon={<FileCheck2 size={20} />}
        />
        <StatCard label={t("bidder.verifiedDocuments")} value={`${d.verified_document_count}/${d.total_document_count}`} tone="info" icon={<FileCheck2 size={20} />} />
        <StatCard label={t("bidder.eligibleTenders")} value={`${d.eligible_tender_count}/${d.open_tender_count}`} icon={<CheckCircle2 size={20} />} />
        <StatCard label={t("bidder.pendingApplications")} value={d.pending_applications} icon={<ClipboardList size={20} />} />
        <StatCard label={t("bidder.clarificationRequests")} value={d.clarification_requests} tone={d.clarification_requests > 0 ? "warning" : "default"} icon={<HelpCircle size={20} />} />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>{t("common.openTenders")} · {t("bidder.eligibleTenders")}</CardTitle>
            <Link href="/bidder/tenders" className="text-sm font-medium text-navy-700 hover:underline">{t("common.view")} →</Link>
          </CardHeader>
          <CardContent className="divide-y divide-slate-100">
            {tenders.slice(0, 6).map((td) => (
              <Link key={td.id} href={`/bidder/tenders/${td.id}`} className="flex items-center justify-between gap-3 rounded-md px-1 py-3 hover:bg-slate-50">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-slate-900">{td.title}</div>
                  <div className="text-xs text-slate-500">
                    {td.tender_no} · {td.department}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge variant={eligibilityVariant(td.eligibility_badge)}>
                    {eligibilityLabel(td.eligibility_badge)}
                  </Badge>
                  <span className="text-xs font-semibold text-slate-700">{td.value_max ? formatINR(td.value_max) : "-"}</span>
                </div>
              </Link>
            ))}
            {tenders.length === 0 && <p className="py-4 text-sm text-slate-500">{t("common.noData")}</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>{t("bidder.pendingApplications")}</CardTitle>
            <Link href="/bidder/applications" className="text-sm font-medium text-navy-700 hover:underline">{t("common.view")} →</Link>
          </CardHeader>
          <CardContent className="divide-y divide-slate-100">
            {d.applications.slice(0, 6).map((a) => (
              <Link key={a.id} href={`/bidder/applications/${a.id}`} className="flex items-center justify-between gap-3 rounded-md px-1 py-3 hover:bg-slate-50">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-slate-900">{a.application_no}</div>
                  <div className="text-xs text-slate-500">{a.tender} · {a.submitted_at ? fmtDate(a.submitted_at) : "-"}</div>
                </div>
                <AppStatusBadge status={a.status} />
              </Link>
            ))}
            {d.applications.length === 0 && <p className="py-4 text-sm text-slate-500">{t("common.noData")}</p>}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><CalendarClock size={16} /> {t("bidder.deadlineReminders")}</CardTitle>
        </CardHeader>
        <CardContent className="divide-y divide-slate-100">
          {d.reminders.map((r) => (
            <div key={r.tender_no} className="flex items-center justify-between py-2.5">
              <Link href="/bidder/tenders" className="min-w-0 truncate pr-3 text-sm text-slate-700 hover:underline">
                {r.tender_no} - {r.title}
              </Link>
              <Badge variant={r.days_left <= 2 ? "danger" : r.days_left <= 5 ? "warning" : "slate"}>
                {t("common.daysLeft", { n: r.days_left })} · {fmtDate(r.close_date)}
              </Badge>
            </div>
          ))}
          {d.reminders.length === 0 && (
            <p className="py-4 text-sm text-slate-500">
              {tenders.length > 0 ? `Next deadline: ${tenders[0].tender_no} - ${fmtDate(tenders[0].close_date)}` : t("common.noData")}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
