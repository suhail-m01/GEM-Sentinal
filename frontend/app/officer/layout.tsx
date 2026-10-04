"use client";

import { Guard } from "@/lib/auth";
import { Shell } from "@/components/app/shell";

// Migration window: the legacy officer console is the workspace for every
// buyer-side role (mirrors backend OFFICER_ROLES / get_current_officer).
// Per-role data scoping is enforced in the backend; the tender-scoped
// workspaces of Slice 4+ replace this bridge.
const OFFICER_CONSOLE_ROLES = [
  "L1_OFFICER",
  "PROCUREMENT_OFFICER",
  "BID_OPENING_OFFICER",
  "TECHNICAL_EVALUATOR",
  "FINANCE_EVALUATOR",
  "COMMITTEE_CHAIR",
  "COMMITTEE_MEMBER",
  "CFA",
  "INDENTOR",
  "CONTRACT_MANAGER",
  "CONTRACT_OFFICER",
  "INSPECTION_OFFICER",
  "PAYMENT_OFFICER",
] as const;

export default function OfficerLayout({ children }: { children: React.ReactNode }) {
  return (
    <Guard role={[...OFFICER_CONSOLE_ROLES]}>
      <Shell>{children}</Shell>
    </Guard>
  );
}
