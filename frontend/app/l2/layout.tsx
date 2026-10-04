"use client";

import { Guard } from "@/lib/auth";
import { Shell } from "@/components/app/shell";

// Migration window: the legacy approval console is the workspace for
// approval/financial roles (mirrors backend L2_BEHAVIOR_ROLES). It is
// replaced by committee + CFA workflows in Slice 7.
const APPROVAL_CONSOLE_ROLES = ["L2_OFFICER", "FINANCE_EVALUATOR", "CFA"] as const;

export default function L2Layout({ children }: { children: React.ReactNode }) {
  return (
    <Guard role={[...APPROVAL_CONSOLE_ROLES]}>
      <Shell>{children}</Shell>
    </Guard>
  );
}
