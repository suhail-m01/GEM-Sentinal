"use client";

import React, { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Building2, Landmark, Loader2, ShieldCheck, Smartphone, IdCard } from "lucide-react";
import { useAuth, type LoginChallenge } from "@/lib/auth";
import type { User } from "@/lib/types";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/fields";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Portal = "bidder" | "gov";

interface OtpChallenge {
  otp_required: boolean;
  mobile_masked?: string | null;
  expires_in?: number;
  provider?: string;
  sms_error?: string | null;
  /** present only when no SMS gateway is configured (console/demo mode) */
  dev_otp?: string | null;
  pending_approval?: boolean;
  message?: string;
  access_token?: string;
  refresh_token?: string;
  user?: User;
}

const DEMO_BIDDERS: Array<{ label: string; email: string; password: string; hint?: string }> = [
  { label: "Bidder A - clean pass", email: "ramesh@bidder.in", password: "Bidder@1234", hint: "Scenario A" },
  { label: "Bidder B - compliance gap", email: "sita@bidder.in", password: "Bidder@1234", hint: "Scenario B" },
  { label: "Bidder C - blacklisted", email: "vikram@bidder.in", password: "Bidder@1234", hint: "Scenario C" },
  { label: "Bidder D - source unavailable", email: "meena@bidder.in", password: "Bidder@1234", hint: "Scenario D" },
];

// Ministry of Petroleum & Natural Gas civil-servant ID format:
// GOI (Govt of India) - PNG (ministry) - year of joining - employee no.
const DEMO_GOV: Array<{ label: string; gov_id: string; hint?: string }> = [
  { label: "System Admin", gov_id: "GOI-PNG-2011-0114" },
  { label: "Auditor (read-only)", gov_id: "GOI-PNG-2009-0387" },
  { label: "Procurement Officer - Belagavi", gov_id: "GOI-PNG-2018-0492" },
  { label: "Procurement Officer - Hubli", gov_id: "GOI-PNG-2016-0733" },
  { label: "Finance Evaluator", gov_id: "GOI-PNG-2014-0265" },
  { label: "Technical Evaluator", gov_id: "GOI-PNG-2017-0548" },
  { label: "Bid Opening Officer", gov_id: "GOI-PNG-2015-0621" },
  { label: "Committee Chair", gov_id: "GOI-PNG-2010-0233" },
  { label: "Committee Member", gov_id: "GOI-PNG-2013-0876" },
  { label: "CFA", gov_id: "GOI-PNG-2012-0349" },
  { label: "Contract Manager", gov_id: "GOI-PNG-2016-0912" },
  { label: "Contract Execution Officer", gov_id: "GOI-PNG-2014-0505" },
  { label: "Inspection & Delivery Officer", gov_id: "GOI-PNG-2015-0688" },
  { label: "Payment & Settlement Officer", gov_id: "GOI-PNG-2017-0771" },
  { label: "Officer awaiting approval", gov_id: "GOI-PNG-2025-1102", hint: "blocked until admin approves" },
];

function LoginInner() {
  const t = useTranslations();
  const { login, completeLoginOtp, resendLoginOtp } = useAuth();
  const search = useSearchParams();
  const initial: Portal = search.get("portal") === "gov" ? "gov" : "bidder";

  const [portal, setPortal] = useState<Portal>(initial);
  const [phase, setPhase] = useState<"first" | "otp">("first");
  // bidder
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // gov
  const [govId, setGovId] = useState("");
  // otp step
  const [challenge, setChallenge] = useState<OtpChallenge | null>(null);
  const [otpCode, setOtpCode] = useState("");
  const [trustDevice, setTrustDevice] = useState(true);
  const [pendingMsg, setPendingMsg] = useState("");
  const [resendAt, setResendAt] = useState(0);
  const [now, setNow] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (phase !== "otp") return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [phase]);

  const go = (u: User) => {
    window.location.href = u.role === "BIDDER" ? "/bidder" : u.role === "ADMIN" ? "/admin" : "/officer";
  };

  const reset = (p: Portal) => {
    setPortal(p);
    setPhase("first");
    setChallenge(null);
    setOtpCode("");
    setError("");
    setPendingMsg("");
  };

  const fillBidder = (acc: { email: string; password: string }) => {
    setPortal("bidder");
    setEmail(acc.email);
    setPassword(acc.password);
    setPhase("first");
    setChallenge(null);
    setError("");
    setPendingMsg("");
  };

  const fillGov = (gov_id: string) => {
    setPortal("gov");
    setGovId(gov_id);
    setPhase("first");
    setChallenge(null);
    setError("");
    setPendingMsg("");
  };

  // --- Step 1: bidder (email+password) ---
  const submitBidderCreds = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setPendingMsg("");
    setBusy(true);
    try {
      const ch = await login(email, password);
      if (!ch.otp_required && ch.user) {
        go(ch.user);
        return;
      }
      setChallenge(ch);
      setOtpCode("");
      setPhase("otp");
      setResendAt(Date.now() + 60_000);
    } catch (err: any) {
      const det = err?.detail;
      setError(typeof det === "string" ? det : det?.message ?? t("auth.loginFailed"));
    } finally {
      setBusy(false);
    }
  };

  // --- Step 1: gov (Government ID → OTP to registered phone) ---
  const sendGovOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setPendingMsg("");
    setBusy(true);
    try {
      const ch = await api<OtpChallenge>("/auth/gov-login", {
        method: "POST",
        body: JSON.stringify({ gov_id: govId.trim() }),
      });
      setChallenge(ch);
      setOtpCode("");
      setPhase("otp");
      setResendAt(Date.now() + 60_000);
    } catch (err: any) {
      const det = err?.detail;
      setError(typeof det === "string" ? det : det?.message ?? String(err));
    } finally {
      setBusy(false);
    }
  };

  // --- Step 2: verify OTP (bidder or gov) ---
  const submitOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setPendingMsg("");
    setBusy(true);
    try {
      if (portal === "bidder") {
        const u = await completeLoginOtp(email, otpCode, trustDevice);
        go(u);
      } else {
        const r = await api<OtpChallenge>("/auth/gov-verify", {
          method: "POST",
          body: JSON.stringify({ gov_id: govId.trim(), otp: otpCode }),
        });
        if (r.pending_approval) {
          setPendingMsg(r.message ?? t("auth.govPending"));
          return;
        }
        if (r.user && r.access_token) {
          // mirror the bidder flow: store tokens, then redirect
          import("@/lib/api").then(({ setTokens }) => {
            setTokens(r.access_token!, r.refresh_token);
            go(r.user!);
          });
        }
      }
    } catch (err: any) {
      const det = err?.detail;
      setError(typeof det === "string" ? det : det?.message ?? String(err));
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    setError("");
    try {
      if (portal === "bidder") {
        const ch = await resendLoginOtp(email);
        setChallenge({ ...challenge, ...ch } as OtpChallenge);
      } else {
        const ch = await api<OtpChallenge>("/auth/gov-login", {
          method: "POST",
          body: JSON.stringify({ gov_id: govId.trim() }),
        });
        setChallenge(ch);
      }
      setResendAt(Date.now() + 60_000);
    } catch (err: any) {
      const det = err?.detail;
      setError(typeof det === "string" ? det : det?.message ?? String(err));
    }
  };

  const resendLeft = Math.max(0, Math.ceil((resendAt - now) / 1000));

  return (
    <div className="flex min-h-screen flex-col bg-gradient-to-br from-navy-950 via-navy-900 to-navy-800">
      <div className="flex w-full flex-1 items-center justify-center p-4">
      <div className="w-full max-w-3xl">
        <Link href="/" className="mb-4 inline-flex items-center gap-1.5 text-sm text-navy-200 hover:text-white">
          ← <span className="font-semibold">{t("common.appName")}</span>
        </Link>
        <div className="overflow-hidden rounded-2xl bg-white shadow-2xl">
          {/* Portal tabs */}
          <div className="grid grid-cols-2">
            <button
              onClick={() => reset("bidder")}
              className={cn(
                "flex items-center justify-center gap-2 border-b-2 px-4 py-4 text-sm font-bold transition",
                portal === "bidder" ? "border-accent-500 bg-accent-50/50 text-navy-900" : "border-transparent text-slate-400 hover:text-slate-600"
              )}
            >
              <Building2 size={17} className={portal === "bidder" ? "text-accent-600" : ""} /> Bidder Portal
            </button>
            <button
              onClick={() => reset("gov")}
              className={cn(
                "flex items-center justify-center gap-2 border-b-2 px-4 py-4 text-sm font-bold transition",
                portal === "gov" ? "border-sky-600 bg-sky-50/50 text-navy-900" : "border-transparent text-slate-400 hover:text-slate-600"
              )}
            >
              <Landmark size={17} className={portal === "gov" ? "text-sky-600" : ""} /> Government Portal
            </button>
          </div>

          <div className="p-8">
            <div className="flex items-center gap-2.5">
              <div className={cn("flex h-10 w-10 items-center justify-center rounded-lg text-white", portal === "bidder" ? "bg-accent-500" : "bg-sky-600")}>
                {phase === "otp" ? <Smartphone size={22} /> : <ShieldCheck size={22} />}
              </div>
              <div>
                <h2 className="text-lg font-bold text-navy-900">
                  {t("auth.loginTitle")}: {portal === "bidder" ? "Bidder" : "Government"}
                  {phase === "otp" && <span className="ml-2 text-sm font-medium text-slate-500">· {t("auth.otpStepTitle")}</span>}
                </h2>
                <p className="text-xs text-slate-500">
                  {portal === "bidder" ? t("common.principle") : t("auth.govLoginNote")}
                </p>
              </div>
            </div>

            {phase === "first" ? (
              portal === "bidder" ? (
                <form onSubmit={submitBidderCreds} className="mt-6 space-y-4">
                  {error && <Alert variant="danger">{error}</Alert>}
                  <div>
                    <Label htmlFor="email">{t("auth.email")}</Label>
                    <Input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="ramesh@bidder.in" />
                  </div>
                  <div>
                    <Label htmlFor="password">{t("auth.password")}</Label>
                    <Input id="password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" />
                  </div>
                  <p className="text-[11px] text-slate-400">{t("auth.twofactorNote")}</p>
                  <Button type="submit" className="w-full" size="lg" loading={busy}>
                    {busy ? <Loader2 size={16} className="animate-spin" /> : t("auth.signIn")}
                  </Button>
                </form>
              ) : (
                <form onSubmit={sendGovOtp} className="mt-6 space-y-4">
                  {error && <Alert variant="danger">{error}</Alert>}
                  <div>
                    <Label htmlFor="gov_id">{t("auth.govId")}</Label>
                    <Input
                      id="gov_id"
                      required
                      value={govId}
                      onChange={(e) => setGovId(e.target.value.toUpperCase())}
                      placeholder="GOI-PNG-2018-0492"
                      className="font-mono tracking-widest"
                    />
                  </div>
                  <p className="text-[11px] text-slate-400">{t("auth.govOtpNote")}</p>
                  <Button type="submit" className="w-full" size="lg" loading={busy}>
                    {busy ? <Loader2 size={16} className="animate-spin" /> : <><Smartphone size={16} className="mr-1 inline" />{t("auth.sendOtp")}</>}
                  </Button>
                </form>
              )
            ) : (
              <form onSubmit={submitOtp} className="mt-6 space-y-4">
                {error && <Alert variant="danger">{error}</Alert>}
                {pendingMsg && <Alert variant="warning" title={t("auth.govPendingTitle")}>{pendingMsg}</Alert>}
                {challenge?.sms_error && (
                  <Alert variant="danger" title={t("auth.smsError")}>{challenge.sms_error}</Alert>
                )}
                {challenge?.pending_approval && !pendingMsg && (
                  <Alert variant="warning" title={t("auth.govPendingTitle")}>{t("auth.govPendingHint")}</Alert>
                )}
                <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
                  <div className="flex items-center gap-2 text-slate-600">
                    <Smartphone size={15} />
                    <span>{t("auth.otpSentTo")} <b className="font-mono">{challenge?.mobile_masked ?? "-"}</b></span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-400">{t("auth.otpExpiry")}</div>
                </div>
                <div>
                  <Label htmlFor="otp">{t("auth.otpLabel")}</Label>
                  <Input
                    id="otp"
                    inputMode="numeric"
                    pattern="[0-9]*"
                    maxLength={6}
                    required
                    autoFocus
                    className="text-center font-mono text-lg tracking-[0.5em]"
                    value={otpCode}
                    onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, ""))}
                    placeholder="••••••"
                  />
                </div>
                {challenge?.dev_otp && (
                  <div className="flex items-center justify-between gap-2 rounded-md border border-dashed border-amber-300 bg-amber-50 px-2.5 py-1.5">
                    <span className="text-[11px] text-amber-800">Console delivery - tap to fill</span>
                    <button
                      type="button"
                      onClick={() => setOtpCode(challenge!.dev_otp!)}
                      className="font-mono text-sm font-bold tracking-[0.3em] text-amber-900 hover:underline"
                    >
                      {challenge.dev_otp}
                    </button>
                  </div>
                )}
                {portal === "bidder" && (
                  <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-600">
                    <input
                      type="checkbox"
                      checked={trustDevice}
                      onChange={(e) => setTrustDevice(e.target.checked)}
                      className="h-4 w-4 rounded border-slate-300"
                    />
                    {t("auth.trustDevice")}
                  </label>
                )}
                <Button type="submit" className="w-full" size="lg" loading={busy}>
                  {busy ? <Loader2 size={16} className="animate-spin" /> : t("auth.verifyAndSignIn")}
                </Button>
                <div className="flex items-center justify-between text-sm">
                  <button type="button" onClick={() => { setPhase("first"); setChallenge(null); setError(""); setOtpCode(""); setPendingMsg(""); }}
                          className="font-medium text-slate-500 hover:text-slate-700">
                    ← {t("auth.backToCreds")}
                  </button>
                  <button type="button" onClick={resend} disabled={resendLeft > 0 || busy}
                          className="font-medium text-navy-700 hover:underline disabled:cursor-not-allowed disabled:text-slate-300">
                    {resendLeft > 0 ? t("auth.resendIn", { s: resendLeft }) : t("auth.resendOtp")}
                  </button>
                </div>
              </form>
            )}

            {/* Demo accounts */}
            <div className="mt-6">
              <div className="mb-2 flex items-center gap-2">
                <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("auth.demoAccounts")}</span>
                <Badge variant="slate">{portal === "bidder" ? "click to autofill" : "click to prefill Govt ID"}</Badge>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {portal === "bidder"
                  ? DEMO_BIDDERS.map((acc) => (
                      <button
                        key={acc.email}
                        type="button"
                        onClick={() => fillBidder(acc)}
                        className={cn(
                          "rounded-md border px-3 py-2 text-left text-xs transition hover:shadow",
                          email === acc.email ? "border-navy-500 bg-navy-50 ring-1 ring-navy-300" : "border-slate-200 bg-slate-50 hover:border-navy-300"
                        )}
                      >
                        <div className="flex items-center gap-1.5 font-semibold text-slate-800">
                          <IdCard size={12} /> {acc.label}
                        </div>
                        <div className="mt-0.5 font-mono text-[11px] text-slate-500">{acc.email} / {acc.password}</div>
                        {acc.hint && <div className="mt-0.5 text-[10px] text-amber-700">{acc.hint}</div>}
                      </button>
                    ))
                  : DEMO_GOV.map((acc) => (
                      <button
                        key={acc.gov_id}
                        type="button"
                        onClick={() => fillGov(acc.gov_id)}
                        className={cn(
                          "rounded-md border px-3 py-2 text-left text-xs transition hover:shadow",
                          govId === acc.gov_id ? "border-sky-500 bg-sky-50 ring-1 ring-sky-300" : "border-slate-200 bg-slate-50 hover:border-sky-300"
                        )}
                      >
                        <div className="flex items-center gap-1.5 font-semibold text-slate-800">
                          <Landmark size={12} /> {acc.label}
                        </div>
                        <div className="mt-0.5 font-mono text-[11px] text-slate-500">{acc.gov_id}</div>
                        {acc.hint && <div className="mt-0.5 text-[10px] text-amber-700">{acc.hint}</div>}
                      </button>
                    ))}
              </div>
            </div>

            <div className="mt-6 border-t border-slate-100 pt-4 text-center text-sm text-slate-600">
              {portal === "bidder" ? (
                <>
                  {t("auth.noAccount")}{" "}
                  <Link href="/register" className="font-semibold text-navy-700 hover:underline">→</Link>
                </>
              ) : (
                <span className="text-xs">{t("auth.govApprovalNote")}</span>
              )}
            </div>
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginInner />
    </Suspense>
  );
}
