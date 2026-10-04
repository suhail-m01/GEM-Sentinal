"use client";

import React from "react";
import { useTranslations } from "next-intl";
import {
  Badge,
  eligibilityVariant,
  riskVariant,
  statusVariant,
} from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { FolderOpen } from "lucide-react";

/* ---------- Stat card ---------- */
export function StatCard({
  label,
  value,
  sub,
  tone = "default",
  icon,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "default" | "success" | "warning" | "danger" | "critical" | "info";
  icon?: React.ReactNode;
}) {
  const tones: Record<string, string> = {
    default: "border-slate-200",
    success: "border-emerald-200 bg-emerald-50/40",
    warning: "border-amber-200 bg-amber-50/40",
    danger: "border-red-200 bg-red-50/40",
    critical: "border-critical-600/40 bg-critical-600/5",
    info: "border-sky-200 bg-sky-50/40",
  };
  return (
    <Card className={cn("p-4", tones[tone])}>
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
          <div className="mt-1 text-2xl font-bold text-navy-900">{value}</div>
          {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
        </div>
        {icon && <div className="text-slate-400">{icon}</div>}
      </div>
    </Card>
  );
}

/* ---------- Score ring / gauge ---------- */
export function ScoreRing({
  score,
  risk,
  size = 120,
  label,
}: {
  score: number | null | undefined;
  risk: string | null | undefined;
  size?: number;
  label?: string;
}) {
  const t = useTranslations("risks");
  const s = score ?? 0;
  const r = (size - 14) / 2;
  const c = 2 * Math.PI * r;
  const color =
    risk === "LOW" ? "#059669" : risk === "MEDIUM" ? "#d97706" : risk === "HIGH" ? "#dc2626" : risk === "CRITICAL" ? "#b91c1c" : "#94a3b8";
  return (
    <div className="flex flex-col items-center gap-1">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e2e8f0" strokeWidth="10" />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            stroke={color}
            strokeWidth="10"
            strokeDasharray={c}
            strokeDashoffset={c - (c * s) / 100}
            strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-bold text-navy-900">{score === null || score === undefined ? "-" : s}</span>
          <span className="text-[10px] uppercase tracking-wide text-slate-500">{label ?? "%"}</span>
        </div>
      </div>
      {risk && <Badge variant={riskVariant(risk)}>{t(risk)}</Badge>}
    </div>
  );
}

/* ---------- Status badges (translated) ---------- */
export function AppStatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    "AI Verification Not Run": "AI Verification Not Run",
    "AI Verification In Progress": "AI Verification In Progress",
    "Verification Completed": "Verification Completed",
    "Clarification Requested": "Clarification Requested",
    "Resubmitted": "Resubmitted",
    "L1 Recommended": "L1 Recommended",
    "Pending L2 Approval": "Pending L2 Approval",
    "Admin Co-sign Required": "Admin Co-sign Required",
    Approved: "Approved",
    Rejected: "Rejected",
    Withdrawn: "Withdrawn",
  };
  return <Badge variant={statusVariant(status)}>{map[status] ?? status}</Badge>;
}

export function RiskBadge({ risk }: { risk: string | null | undefined }) {
  const t = useTranslations("risks");
  if (!risk) return <Badge variant="slate">-</Badge>;
  return <Badge variant={riskVariant(risk)}>{t(risk)}</Badge>;
}

export function EligibilityBadge({ value }: { value: string | null | undefined }) {
  const t = useTranslations("eligibility");
  if (!value) return <Badge variant="slate">-</Badge>;
  return <Badge variant={eligibilityVariant(value)}>{t(value)}</Badge>;
}

export function ResultBadge({ result }: { result: string }) {
  const t = useTranslations("results");
  const v =
    result === "PASS" ? "success" : result === "FAIL" ? "danger" : result === "UNABLE_TO_VERIFY" ? "warning" : "slate";
  return <Badge variant={v as any}>{t(result)}</Badge>;
}

/* ---------- Timeline ---------- */
export function Timeline({ events }: { events: Array<{ timestamp: string; action: string; actor: string; actor_role: string; description?: string }> }) {
  if (!events.length) return <p className="text-sm text-slate-500">-</p>;
  return (
    <ol className="relative ml-2 space-y-4 border-l border-slate-200 pl-5">
      {events.map((e, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[26px] top-1 h-3 w-3 rounded-full border-2 border-white bg-navy-700" />
          <div className="text-sm font-medium text-slate-900">{e.action}</div>
          <div className="text-xs text-slate-500">
            {e.actor} ({e.actor_role}) · {new Date(e.timestamp).toLocaleString("en-IN")}
          </div>
          {e.description && <div className="mt-0.5 text-xs text-slate-600">{e.description}</div>}
        </li>
      ))}
    </ol>
  );
}

/* ---------- Empty state ---------- */
export function Empty({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-md border border-dashed border-slate-300 py-12 text-slate-400">
      <FolderOpen className="h-8 w-8" />
      <p className="text-sm">{text}</p>
    </div>
  );
}

/* ---------- Loading ---------- */
export function Loading() {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-slate-500">
      <span className="h-5 w-5 animate-spin rounded-full border-2 border-navy-300 border-t-navy-800" />
      <span className="text-sm">Loading…</span>
    </div>
  );
}
