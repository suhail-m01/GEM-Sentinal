"use client";

import React from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Building2, FolderOpen, Landmark, ShieldCheck, Gavel, SearchCheck } from "lucide-react";

export default function LandingClient() {
  const t = useTranslations();
  return (
    <div className="flex min-h-screen flex-col bg-gradient-to-br from-navy-950 via-navy-900 to-navy-800">
      {/* Header */}
      <header className="mx-auto flex max-w-6xl items-center justify-between px-5 py-5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent-500 text-white">
            <ShieldCheck size={22} />
          </div>
          <div className="leading-tight">
            <div className="text-lg font-bold text-white">{t("common.appName")}</div>
            <div className="text-[11px] text-navy-200">{t("common.tagline")}</div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <a
            href="/login?portal=bidder"
            className="rounded-md border border-navy-600 px-4 py-2 text-sm font-medium text-navy-100 hover:bg-navy-800"
          >
            {t("landing.getStarted")}
          </a>
        </div>
      </header>

      {/* Hero */}
      <section className="mx-auto max-w-6xl px-5 pt-10 pb-14 text-center">
        <h1 className="mx-auto max-w-3xl text-4xl font-extrabold leading-tight text-white md:text-5xl">
          {t("landing.heroTitle")}
        </h1>
        <p className="mx-auto mt-4 max-w-2xl text-base text-navy-200">{t("landing.heroSub")}</p>
        <p className="mt-3 text-sm font-semibold tracking-wide text-accent-300">{t("common.principle")}</p>
      </section>

      {/* Dual portal entry */}
      <section className="mx-auto grid max-w-5xl gap-6 px-5 pb-14 md:grid-cols-2">
        {/* Bidder portal */}
        <Link
          href="/login?portal=bidder"
          className="group rounded-2xl border border-navy-700 bg-navy-900/70 p-7 text-left shadow-xl transition hover:border-accent-400 hover:bg-navy-800/80"
        >
          <div className="flex items-center justify-between">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-500/20 text-accent-300">
              <Building2 size={26} />
            </div>
            <span className="text-2xl text-navy-500 transition group-hover:translate-x-1 group-hover:text-accent-300">→</span>
          </div>
          <h2 className="mt-4 text-2xl font-bold text-white">Bidder Portal</h2>
          <p className="mt-1 text-sm font-medium text-accent-300">For companies & MSMEs applying to tenders</p>
          <ul className="mt-4 space-y-2 text-sm text-navy-200">
            <li className="flex gap-2"><SearchCheck size={15} className="mt-0.5 shrink-0 text-accent-300" /> Find tenders with live eligibility pre-check</li>
            <li className="flex gap-2"><FolderOpen size={15} className="mt-0.5 shrink-0 text-accent-300" /> Verified Document Vault - verify once, apply many</li>
            <li className="flex gap-2"><ShieldCheck size={15} className="mt-0.5 shrink-0 text-accent-300" /> Consent-controlled government data verification</li>
            <li className="flex gap-2"><Landmark size={15} className="mt-0.5 shrink-0 text-accent-300" /> Track applications, respond to clarifications, file appeals</li>
          </ul>
          <div className="mt-6 inline-flex items-center rounded-md bg-accent-600 px-5 py-2.5 text-sm font-semibold text-white transition group-hover:bg-accent-700">
            Enter Bidder Portal →
          </div>
        </Link>

        {/* Government portal */}
        <Link
          href="/login?portal=gov"
          className="group rounded-2xl border border-navy-700 bg-navy-900/70 p-7 text-left shadow-xl transition hover:border-sky-400 hover:bg-navy-800/80"
        >
          <div className="flex items-center justify-between">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-sky-500/20 text-sky-300">
              <Landmark size={26} />
            </div>
            <span className="text-2xl text-navy-500 transition group-hover:translate-x-1 group-hover:text-sky-300">→</span>
          </div>
          <h2 className="mt-4 text-2xl font-bold text-white">Government Portal</h2>
          <p className="mt-1 text-sm font-medium text-sky-300">For procurement officers, evaluators, committee, CFA & system admins</p>
          <ul className="mt-4 space-y-2 text-sm text-navy-200">
            <li className="flex gap-2"><SearchCheck size={15} className="mt-0.5 shrink-0 text-sky-300" /> Tender-scoped queues with AI verification & evidence reports</li>
            <li className="flex gap-2"><Gavel size={15} className="mt-0.5 shrink-0 text-sky-300" /> Technical & financial evaluation, committee recommendation, CFA decision</li>
            <li className="flex gap-2"><ShieldCheck size={15} className="mt-0.5 shrink-0 text-sky-300" /> Override Accountability: re-auth, co-sign, vigilance referrals</li>
            <li className="flex gap-2"><Landmark size={15} className="mt-0.5 shrink-0 text-sky-300" /> Rule Builder, IP-aware hash-chained audit, analytics</li>
          </ul>
          <div className="mt-6 inline-flex items-center rounded-md bg-sky-600 px-5 py-2.5 text-sm font-semibold text-white transition group-hover:bg-sky-500">
            Enter Government Portal →
          </div>
        </Link>
      </section>

      {/* Features */}
      <section className="mx-auto max-w-6xl px-5 pb-16">
        <div className="grid gap-4 md:grid-cols-3">
          <div className="rounded-xl border border-navy-800 bg-navy-900/50 p-5">
            <div className="text-sm font-bold text-white">{t("landing.card1Title")}</div>
            <p className="mt-2 text-xs leading-relaxed text-navy-300">{t("landing.card1Text")}</p>
          </div>
          <div className="rounded-xl border border-navy-800 bg-navy-900/50 p-5">
            <div className="text-sm font-bold text-white">{t("landing.card2Title")}</div>
            <p className="mt-2 text-xs leading-relaxed text-navy-300">{t("landing.card2Text")}</p>
          </div>
          <div className="rounded-xl border border-navy-800 bg-navy-900/50 p-5">
            <div className="text-sm font-bold text-white">{t("landing.card3Title")}</div>
            <p className="mt-2 text-xs leading-relaxed text-navy-300">{t("landing.card3Text")}</p>
          </div>
        </div>
        <p className="mt-10 text-center text-xs text-navy-400">{t("common.decisionSupport")}</p>
      </section>

      {/* Footer */}
      <footer className="mt-auto border-t border-navy-800 bg-navy-950/60">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-2 px-5 py-5 text-xs text-navy-300 md:flex-row">
          <p>© 2026 Central Public Chemical Limited, Ministry of Petroleum and Gas, Government of India.</p>
          <div className="flex gap-5">
            <Link href="/privacy" className="hover:text-white">
              Privacy Policy
            </Link>
            <Link href="/terms" className="hover:text-white">
              Terms of Use
            </Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
