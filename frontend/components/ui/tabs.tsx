"use client";

import React from "react";
import { cn } from "@/lib/utils";

export interface TabDef {
  id: string;
  label: React.ReactNode;
  badge?: number;
}

export function Tabs({
  tabs,
  active,
  onChange,
  className,
}: {
  tabs: TabDef[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap gap-1 border-b border-slate-200", className)} role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={active === t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            "relative -mb-px rounded-t-md border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            active === t.id
              ? "border-navy-900 text-navy-900"
              : "border-transparent text-slate-500 hover:text-slate-800"
          )}
        >
          {t.label}
          {t.badge !== undefined && t.badge > 0 && (
            <span className="ml-1.5 rounded-full bg-navy-900 px-1.5 py-0.5 text-[10px] font-semibold text-white">
              {t.badge}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
