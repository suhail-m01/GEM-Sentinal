"use client";

import { Guard } from "@/lib/auth";
import { Shell } from "@/components/app/shell";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <Guard role="ADMIN">
      <Shell>{children}</Shell>
    </Guard>
  );
}
