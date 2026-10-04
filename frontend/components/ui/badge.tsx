import React from "react";
import { cn } from "@/lib/utils";

type Variant =
  | "default" | "success" | "warning" | "danger" | "critical" | "info"
  | "outline" | "purple" | "slate";

const variants: Record<Variant, string> = {
  default: "bg-navy-100 text-navy-800 border-navy-200",
  success: "bg-emerald-50 text-emerald-700 border-emerald-200",
  warning: "bg-amber-50 text-amber-700 border-amber-200",
  danger: "bg-red-50 text-red-700 border-red-200",
  critical: "bg-critical-600 text-white border-critical-700",
  info: "bg-sky-50 text-sky-700 border-sky-200",
  outline: "bg-white text-slate-600 border-slate-300",
  purple: "bg-purple-50 text-purple-700 border-purple-200",
  slate: "bg-slate-100 text-slate-600 border-slate-200",
};

export function Badge({
  className,
  variant = "default",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { variant?: Variant }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
        variants[variant],
        className
      )}
      {...props}
    />
  );
}

export function riskVariant(risk: string | null | undefined): Variant {
  switch (risk) {
    case "LOW": return "success";
    case "MEDIUM": return "warning";
    case "HIGH": return "danger";
    case "CRITICAL": return "critical";
    default: return "slate";
  }
}

export function statusVariant(status: string): Variant {
  if (status === "Approved") return "success";
  if (status === "Rejected") return "danger";
  if (status === "Withdrawn" || status === "Closed") return "slate";
  if (status === "Admin Co-sign Required") return "critical";
  if (status === "L1 Recommended" || status === "Pending L2 Approval") return "purple";
  if (status === "Clarification Requested") return "warning";
  if (status === "Resubmitted") return "info";
  if (status === "AI Verification In Progress") return "info";
  return "default";
}

export function eligibilityVariant(v: string): Variant {
  if (v === "ELIGIBLE") return "success";
  if (v === "CONDITIONAL") return "warning";
  if (v === "MISSING_DOCUMENTS") return "warning";
  if (v === "BLACKLISTED") return "critical";
  if (v === "NOT_ELIGIBLE") return "danger";
  return "slate";
}

export function eligibilityLabel(v: string): string {
  switch (v) {
    case "ELIGIBLE": return "Eligible";
    case "CONDITIONAL": return "Conditional";
    case "MISSING_DOCUMENTS": return "Missing Docs";
    case "BLACKLISTED": return "Blacklisted";
    case "NOT_ELIGIBLE": return "Not Eligible";
    case "NOT_CHECKED": return "-";
    default: return v || "-";
  }
}

export function docStatusVariant(v: string): Variant {
  switch (v) {
    case "VERIFIED": return "success";
    case "PENDING": return "warning";
    case "EXPIRED": return "danger";
    case "MISMATCH": return "danger";
    case "MANUAL_UPLOADED": return "info";
    case "UNABLE_TO_VERIFY": return "warning";
    case "MANUAL_REVIEW_REQUIRED": return "purple";
    default: return "slate";
  }
}

export function authenticityVariant(v: string | null | undefined): Variant {
  switch (v) {
    case "VERIFIED": return "success";
    case "SUSPICIOUS": return "danger";
    case "UNABLE_TO_VERIFY": return "warning";
    case "MANUAL_REVIEW_REQUIRED": return "purple";
    default: return "slate";
  }
}
