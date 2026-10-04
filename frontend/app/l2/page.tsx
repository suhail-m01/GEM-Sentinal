"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatCard, AppStatusBadge, RiskBadge, Loading } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

interface QueueRow {
  id: string;
  application_no: string;
  company: string | null;
  tender_no: string;
  tender_title?: string;
  status: string;
  risk_level: string | null;
  compliance_score: number | null;
  fraud_flagged?: boolean;
  l1_recommendation: {
    action: string;
    remarks: string | null;
    is_overridden: boolean;
    officer: string | null;
    at: string;
  } | null;
  override_history: Array<{
    id: string;
    severity: string;
    reason_category: string;
    status: string;
    admin_cosign_required: boolean;
    admin_cosigned_at: string | null;
    vigilance_referral_id: string | null;
  }>;
}

interface Dash {
  queue: QueueRow[];
  counts: { l1_recommended: number; pending_approval: number; cosign_required: number };
}

const ACTION_LABEL: Record<string, string> = {
  RECOMMEND_QUALIFY: "Recommended qualification",
  RECOMMEND_REJECT: "Recommended rejection",
  REQUEST_CLARIFICATION: "Requested clarification",
  MANUAL_VERIFICATION_REQUIRED: "Manual verification required",
  FORWARD_TO_L2: "Forwarded to L2",
  RAISE_ESCALATION: "Escalation raised",
};

export default function L2Dashboard() {
  const t = useTranslations();
  const [d, setD] = useState<Dash | null>(null);

  useEffect(() => {
    api<Dash>(`/l2/dashboard`).then(setD).catch(() => {});
  }, []);

  if (!d) return <Loading />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("nav.l2Queue")}</h1>
        <p className="text-sm text-slate-500">Applications requiring your approval or escalation action</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label="L1 recommended" value={d.counts.l1_recommended} tone="info" />
        <StatCard label="Pending L2 approval" value={d.counts.pending_approval} />
        <StatCard label="Admin co-sign required" value={d.counts.cosign_required} tone={d.counts.cosign_required > 0 ? "critical" : "default"} />
      </div>

      {d.queue.length === 0 ? (
        <Card className="p-10 text-center text-sm text-slate-500">{t("common.noData")}</Card>
      ) : (
        <div className="space-y-3">
          {d.queue.map((a) => (
            <Card key={a.id} className="p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-bold text-navy-900">{a.application_no}</span>
                    <AppStatusBadge status={a.status} />
                    <RiskBadge risk={a.risk_level} />
                    {a.fraud_flagged && <Badge variant="danger">FRAUD FLAGGED</Badge>}
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {a.company} · {a.tender_no} - {a.tender_title}
                  </div>
                  {a.l1_recommendation && (
                    <div className="mt-2 rounded-md bg-slate-50 p-2 text-xs text-slate-600">
                      <b>L1 ({a.l1_recommendation.officer}):</b> {ACTION_LABEL[a.l1_recommendation.action] ?? a.l1_recommendation.action}
                      {a.l1_recommendation.remarks ? ` - ${a.l1_recommendation.remarks}` : ""}
                      {a.l1_recommendation.is_overridden && <Badge variant="warning" className="ml-1">OVERRIDDEN</Badge>}
                      <span className="ml-1 text-slate-400">{fmtDateTime(a.l1_recommendation.at)}</span>
                    </div>
                  )}
                  {a.override_history.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {a.override_history.map((o) => (
                        <Badge key={o.id} variant={o.status === "PENDING_COSIGN" ? "critical" : o.status === "APPROVED" ? "success" : "slate"}>
                          OVERRIDE {o.severity} · {o.status}
                          {o.admin_cosign_required && !o.admin_cosigned_at && " · CO-SIGN PENDING"}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
                <Link href={`/l2/review/${a.id}`}>
                  <Button size="sm" variant="secondary">{t("common.view")} →</Button>
                </Link>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
