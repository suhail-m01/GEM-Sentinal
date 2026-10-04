"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  Bell,
  Building2,
  CheckCheck,
  ClipboardList,
  FileSearch,
  FolderOpen,
  Gavel,
  Gauge,
  Globe,
  HeartPulse,
  IdCard,
  Landmark,
  LayoutDashboard,
  LogOut,
  Menu,
  ShieldAlert,
  ShieldCheck,
  Users,
  UserCircle,
  Waves,
  X,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import type { AppNotification, Role } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface NavItem {
  href: string;
  labelKey: string;
  icon: React.ComponentType<{ size?: number | string; className?: string }>;
}

const BIDDER_NAV: NavItem[] = [
  { href: "/bidder", labelKey: "nav.dashboard", icon: LayoutDashboard },
  { href: "/bidder/profile", labelKey: "nav.profile", icon: UserCircle },
  { href: "/bidder/consent", labelKey: "nav.consent", icon: ShieldCheck },
  { href: "/bidder/vault", labelKey: "nav.vault", icon: FolderOpen },
  { href: "/bidder/tenders", labelKey: "nav.tenders", icon: Landmark },
  { href: "/bidder/applications", labelKey: "nav.applications", icon: ClipboardList },
  { href: "/bidder/auctions", labelKey: "nav.auctions", icon: Gavel },
  { href: "/bidder/compliance-health", labelKey: "nav.complianceHealth", icon: HeartPulse },
];

const OFFICER_NAV: NavItem[] = [
  { href: "/officer", labelKey: "nav.dashboard", icon: LayoutDashboard },
  { href: "/officer/applications", labelKey: "nav.queue", icon: ClipboardList },
  { href: "/officer/auctions", labelKey: "nav.auctions", icon: Gavel },
  { href: "/officer/watch", labelKey: "nav.watchTower", icon: Waves },
];

const ADMIN_NAV: NavItem[] = [
  { href: "/admin", labelKey: "nav.dashboard", icon: Gauge },
  { href: "/admin/users", labelKey: "nav.users", icon: Users },
  { href: "/admin/gov-employees", labelKey: "nav.govEmployees", icon: IdCard },
  { href: "/admin/tenders", labelKey: "nav.tenderMgmt", icon: Landmark },
  { href: "/admin/organizations", labelKey: "nav.orgs", icon: Building2 },
  { href: "/admin/blacklist", labelKey: "nav.blacklist", icon: ShieldAlert },
  { href: "/admin/overrides", labelKey: "nav.overrides", icon: FileSearch },
  { href: "/admin/escalations", labelKey: "nav.escalations", icon: ShieldAlert },
  { href: "/admin/audit", labelKey: "nav.auditViewer", icon: FileSearch },
  { href: "/admin/analytics", labelKey: "nav.analytics", icon: Gauge },
];

const ROLE_META: Record<string, { label: string; cls: string }> = {
  BIDDER: { label: "Bidder / Client", cls: "bg-sky-100 text-sky-800" },
  BID_PREPARER: { label: "Bid Preparer", cls: "bg-sky-50 text-sky-700" },
  SIGNATORY: { label: "Bid Signatory", cls: "bg-sky-50 text-sky-700" },
  INDENTOR: { label: "User Dept. Representative", cls: "bg-teal-50 text-teal-800" },
  PROCUREMENT_OFFICER: { label: "Procurement Officer", cls: "bg-navy-100 text-navy-800" },
  BID_OPENING_OFFICER: { label: "Bid Opening Officer", cls: "bg-navy-100 text-navy-800" },
  TECHNICAL_EVALUATOR: { label: "Technical Evaluator", cls: "bg-teal-50 text-teal-800" },
  FINANCE_EVALUATOR: { label: "Finance Evaluator", cls: "bg-teal-50 text-teal-800" },
  COMMITTEE_CHAIR: { label: "Committee Chair", cls: "bg-purple-100 text-purple-800" },
  COMMITTEE_MEMBER: { label: "Committee Member", cls: "bg-purple-50 text-purple-700" },
  CFA: { label: "Competent Financial Authority", cls: "bg-amber-100 text-amber-800" },
  CONTRACT_MANAGER: { label: "Contract Manager", cls: "bg-emerald-50 text-emerald-800" },
  CONTRACT_OFFICER: { label: "Contract Execution Officer", cls: "bg-emerald-50 text-emerald-700" },
  INSPECTION_OFFICER: { label: "Inspection Officer", cls: "bg-emerald-50 text-emerald-800" },
  PAYMENT_OFFICER: { label: "Payment Officer", cls: "bg-emerald-50 text-emerald-800" },
  ADMIN: { label: "System Admin", cls: "bg-critical-600 text-white" },
  AUDITOR: { label: "Auditor (read-only)", cls: "bg-slate-200 text-slate-700" },
  L1_OFFICER: { label: "Procurement Officer (legacy)", cls: "bg-navy-100 text-navy-800" },
  L2_OFFICER: { label: "Approving Officer (legacy)", cls: "bg-purple-100 text-purple-800" },
};

export function RoleBadge({ role }: { role: Role }) {
  const m = ROLE_META[role] ?? { label: role, cls: "bg-slate-100 text-slate-700" };
  return <span className={cn("rounded-full px-2.5 py-1 text-[11px] font-semibold", m.cls)}>{m.label}</span>;
}

function useNotifications() {
  const [items, setItems] = useState<AppNotification[]>([]);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const d = await api<{ notifications: AppNotification[] }>(`/notifications`);
      setItems(d.notifications);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => {
      clearInterval(t);
      document.removeEventListener("mousedown", onClick);
    };
  }, [load]);

  const unread = items.filter((n) => !n.is_read).length;
  return { items, open, setOpen, unread, load, ref };
}

export function Shell({ children }: { children: React.ReactNode }) {
  const t = useTranslations();
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const [lang, setLangState] = useState<string>(() =>
    typeof window !== "undefined" ? localStorage.getItem("gemsentinel.locale") ?? "en" : "en"
  );
  const [mobileNav, setMobileNav] = useState(false);
  const { items: notifs, open: notifOpen, setOpen, unread, load, ref: notifRef } = useNotifications();

  if (!user) return null;
  const nav = user.role === "BIDDER" ? BIDDER_NAV : user.role === "ADMIN" ? ADMIN_NAV : OFFICER_NAV;

  const setLang = (locale: string) => {
    setLangState(locale);
    localStorage.setItem("gemsentinel.locale", locale);
    document.cookie = `NEXT_LOCALE=${locale}; path=/; max-age=31536000`;
    window.location.reload();
  };

  const sidebar = (
    <nav className="flex h-full flex-col gap-1 p-3" aria-label="Main navigation">
      {nav.map((item) => {
        const active = item.href === "/officer" ? pathname === "/officer" : pathname.startsWith(item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              active ? "bg-navy-800 text-white" : "text-navy-100 hover:bg-navy-800/60"
            )}
          >
            <item.icon size={17} />
            {t(item.labelKey)}
          </Link>
        );
      })}
      <div className="mt-auto space-y-1 border-t border-navy-800 pt-3">
        <div className="px-3 text-[11px] leading-snug text-navy-300">{t("common.principle")}</div>
      </div>
    </nav>
  );

  return (
    <div className="flex min-h-screen flex-col">
      {/* Top navigation */}
      <header className="sticky top-0 z-40 flex h-14 items-center gap-3 border-b border-navy-800 bg-navy-900 px-4">
        <button
          className="rounded-md p-1.5 text-navy-100 hover:bg-navy-800 md:hidden"
          onClick={() => setMobileNav(!mobileNav)}
          aria-label="Toggle navigation"
        >
          <Menu size={20} />
        </button>
        <Link href={user.role === "BIDDER" ? "/bidder" : user.role === "ADMIN" ? "/admin" : "/officer"} className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-white text-navy-900">
            <ShieldCheck size={18} />
          </div>
          <div className="leading-tight">
            <div className="text-sm font-bold text-white">{t("common.appName")}</div>
            <div className="hidden text-[10px] text-navy-300 sm:block">{t("common.tagline")}</div>
          </div>
        </Link>
        <div className="ml-auto flex items-center gap-2">
          {/* Language switch (global, persisted via cookie) */}
          <div className="flex rounded-md border border-navy-700 p-0.5 text-xs font-semibold" role="group" aria-label="Language">
            <button
              onClick={() => setLang("en")}
              className={cn("rounded px-2 py-1", lang === "hi" ? "text-navy-200" : "bg-white text-navy-900")}
            >
              EN
            </button>
            <button
              onClick={() => setLang("hi")}
              className={cn("rounded px-2 py-1", lang === "hi" ? "bg-white text-navy-900" : "text-navy-200")}
            >
              हिं
            </button>
          </div>

          {/* Notifications bell */}
          <div className="relative" ref={notifRef}>
            <button
              onClick={() => {
                setOpen(!notifOpen);
                load();
              }}
              className="relative rounded-md p-2 text-navy-100 hover:bg-navy-800"
              aria-label={`Notifications (${unread} unread)`}
            >
              <Bell size={18} />
              {unread > 0 && (
                <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-bold text-white">
                  {unread > 9 ? "9+" : unread}
                </span>
              )}
            </button>
            {notifOpen && (
              <div className="absolute right-0 z-50 mt-2 w-96 max-w-[90vw] overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl">
                <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
                  <span className="text-sm font-semibold text-navy-900">{t("common.notifications")}</span>
                  <button
                    className="flex items-center gap-1 text-xs font-medium text-navy-700 hover:underline"
                    onClick={async () => {
                      await api(`/notifications/read-all`, { method: "POST" }).catch(() => {});
                      load();
                    }}
                  >
                    <CheckCheck size={14} /> {t("common.markAllRead")}
                  </button>
                </div>
                <div className="max-h-96 overflow-y-auto">
                  {notifs.length === 0 && <div className="px-3 py-6 text-center text-sm text-slate-500">{t("common.noNotifications")}</div>}
                  {notifs.map((n) => (
                    <button
                      key={n.id}
                      className={cn(
                        "block w-full border-b border-slate-50 px-3 py-2.5 text-left hover:bg-slate-50",
                        !n.is_read && "bg-navy-50/50"
                      )}
                      onClick={async () => {
                        await api(`/notifications/${n.id}/read`, { method: "POST" }).catch(() => {});
                        load();
                      }}
                    >
                      <div className="flex items-center gap-2">
                        {!n.is_read && <span className="h-2 w-2 shrink-0 rounded-full bg-navy-700" />}
                        <span className="truncate text-sm font-medium text-slate-900">{n.title}</span>
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-xs text-slate-600">{n.message}</p>
                      <p className="mt-0.5 text-[10px] text-slate-400">
                        {new Date(n.created_at).toLocaleString("en-IN")}
                      </p>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* User menu */}
          <div className="flex items-center gap-2 border-l border-navy-700 pl-3">
            <div className="hidden text-right leading-tight sm:block">
              <div className="text-xs font-semibold text-white">{user.full_name}</div>
              <div className="text-[10px] text-navy-300">{user.email}</div>
            </div>
            <RoleBadge role={user.role} />
            <button
              onClick={logout}
              className="rounded-md p-2 text-navy-200 hover:bg-navy-800"
              aria-label={t("common.signOut")}
              title={t("common.signOut")}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </header>

      <div className="flex flex-1">
        {/* Sidebar (desktop) */}
        <aside className="sticky top-14 hidden h-[calc(100vh-3.5rem)] w-64 shrink-0 flex-col bg-navy-900 md:flex">
          {sidebar}
        </aside>
        {/* Mobile nav */}
        {mobileNav && (
          <div className="fixed inset-0 z-50 md:hidden">
            <div className="absolute inset-0 bg-navy-950/60" onClick={() => setMobileNav(false)} />
            <div className="absolute left-0 top-0 h-full w-72 bg-navy-900">
              <div className="flex items-center justify-between p-3">
                <span className="text-sm font-bold text-white">{t("common.appName")}</span>
                <button onClick={() => setMobileNav(false)} aria-label="Close navigation">
                  <X size={18} className="text-white" />
                </button>
              </div>
              {sidebar}
            </div>
          </div>
        )}

        <main className="min-w-0 flex-1 p-4 md:p-6">{children}</main>
      </div>

      <footer className="border-t border-slate-200 bg-white px-4 py-3 text-center text-xs text-slate-500">
        {t("common.decisionSupport")}
      </footer>
    </div>
  );
}
