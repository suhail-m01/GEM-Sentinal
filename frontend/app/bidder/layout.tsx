"use client";

import { Guard } from "@/lib/auth";
import { Shell } from "@/components/app/shell";

export default function BidderLayout({ children }: { children: React.ReactNode }) {
  return (
    <Guard role="BIDDER">
      <Shell>{children}</Shell>
    </Guard>
  );
}
