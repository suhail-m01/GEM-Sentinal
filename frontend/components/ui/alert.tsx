import React from "react";
import { AlertCircle, CheckCircle2, Info, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";

type Variant = "info" | "success" | "warning" | "danger" | "critical";

const styles: Record<Variant, { box: string; icon: React.ReactNode }> = {
  info: { box: "border-sky-200 bg-sky-50 text-sky-900", icon: <Info size={16} className="mt-0.5 shrink-0" /> },
  success: { box: "border-emerald-200 bg-emerald-50 text-emerald-900", icon: <CheckCircle2 size={16} className="mt-0.5 shrink-0" /> },
  warning: { box: "border-amber-200 bg-amber-50 text-amber-900", icon: <TriangleAlert size={16} className="mt-0.5 shrink-0" /> },
  danger: { box: "border-red-200 bg-red-50 text-red-900", icon: <AlertCircle size={16} className="mt-0.5 shrink-0" /> },
  critical: { box: "border-critical-600/40 bg-critical-600/10 text-critical-700", icon: <AlertCircle size={16} className="mt-0.5 shrink-0" /> },
};

export function Alert({
  variant = "info",
  title,
  children,
  className,
}: {
  variant?: Variant;
  title?: string;
  children?: React.ReactNode;
  className?: string;
}) {
  const s = styles[variant];
  return (
    <div className={cn("flex gap-2 rounded-md border px-3 py-2.5 text-sm", s.box, className)}>
      {s.icon}
      <div className="min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={title ? "mt-0.5" : ""}>{children}</div>}
      </div>
    </div>
  );
}
