"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { setTokens } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/fields";
import { Alert } from "@/components/ui/alert";

export default function RegisterPage() {
  const t = useTranslations();
  const { refresh } = useAuth();
  const [form, setForm] = useState({
    email: "",
    password: "",
    full_name: "",
    mobile: "",
    company_name: "",
    pan: "",
    gstin: "",
    udyam_number: "",
    business_type: "",
    registered_address: "",
    district: "",
    state: "",
  });
  const [otp, setOtp] = useState<null | {
    email: string; mobileMasked: string | null; devOtp: string | null; smsError: string | null; provider: string | null;
  }>(null);
  const [otpCode, setOtpCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const r = await api<{ mobile_masked?: string; dev_otp?: string | null; sms_error?: string | null; provider?: string }>(`/auth/register`, {
        method: "POST",
        body: JSON.stringify(form),
      });
      setOtp({
        email: form.email,
        mobileMasked: r.mobile_masked ?? null,
        devOtp: r.dev_otp ?? null,
        smsError: r.sms_error ?? null,
        provider: r.provider ?? null,
      });
    } catch (err: any) {
      setError(err?.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  };

  const verify = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const r = await api<{ access_token: string; refresh_token: string }>(`/auth/verify-otp`, {
        method: "POST",
        body: JSON.stringify({ email: otp!.email, otp: otpCode, purpose: "register" }),
      });
      setTokens(r.access_token, r.refresh_token);
      window.location.href = "/bidder";
    } catch (err: any) {
      setError(err?.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col bg-gradient-to-br from-navy-950 via-navy-900 to-navy-800">
      <div className="flex w-full flex-1 items-start justify-center p-4">
      <div className="mx-auto my-8 w-full max-w-2xl rounded-2xl bg-white p-8 shadow-2xl">
        <div className="mb-6 flex items-center gap-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-accent-500 text-white">
            <ShieldCheck size={22} />
          </div>
          <div>
            <h1 className="text-lg font-bold text-navy-900">{t("auth.registerTitle")}</h1>
            <p className="text-xs text-slate-500">{t("common.appName")} - {t("common.tagline")}</p>
          </div>
        </div>

        {error && <Alert variant="danger" className="mb-4">{error}</Alert>}
        {otp && otp.smsError && (
          <Alert variant="danger" className="mb-4" title={t("auth.smsError")}>{otp.smsError}</Alert>
        )}
        {otp && !otp.smsError && (
          <Alert variant="info" className="mb-4" title={t("auth.otpSentTo2", { mobile: otp.mobileMasked ?? "your mobile" })} />
        )}

        {!otp ? (
          <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
            <Field label={t("auth.fullName")} required>
              <Input required value={form.full_name} onChange={set("full_name")} />
            </Field>
            <Field label={t("auth.email")} required>
              <Input type="email" required value={form.email} onChange={set("email")} />
            </Field>
            <Field label={t("auth.password")} required hint="Min 8 characters">
              <Input type="password" required minLength={8} value={form.password} onChange={set("password")} />
            </Field>
            <Field label={t("auth.mobile")} required hint="OTP for sign-in & registration is sent to this number">
              <Input required inputMode="tel" value={form.mobile} onChange={set("mobile")} placeholder="98450 12345" />
            </Field>
            <Field label={t("auth.company")} className="sm:col-span-2">
              <Input value={form.company_name} onChange={set("company_name")} />
            </Field>
            <Field label={t("auth.pan")}>
              <Input value={form.pan} onChange={set("pan")} maxLength={10} placeholder="ABCDE1234F" />
            </Field>
            <Field label={t("auth.gstin")}>
              <Input value={form.gstin} onChange={set("gstin")} maxLength={15} placeholder="29ABCDE1234F1Z5" />
            </Field>
            <Field label={t("auth.udyam")}>
              <Input value={form.udyam_number} onChange={set("udyam_number")} />
            </Field>
            <Field label={t("auth.businessType")}>
              <Select value={form.business_type} onChange={set("business_type")}>
                <option value="">-</option>
                <option>Private Limited</option>
                <option>Public Limited</option>
                <option>Partnership</option>
                <option>Proprietorship</option>
                <option>LLP</option>
              </Select>
            </Field>
            <Field label={t("auth.address")} className="sm:col-span-2">
              <Input value={form.registered_address} onChange={set("registered_address")} />
            </Field>
            <Field label={t("common.district")}>
              <Input value={form.district} onChange={set("district")} />
            </Field>
            <Field label={t("common.state")}>
              <Input value={form.state} onChange={set("state")} />
            </Field>
            <div className="sm:col-span-2">
              <Button type="submit" className="w-full" size="lg" loading={busy}>
                {t("auth.createAccount")}
              </Button>
              <p className="mt-3 text-center text-sm text-slate-600">
                <Link href="/login" className="font-medium text-navy-700 hover:underline">
                  {t("auth.haveAccount")}
                </Link>
              </p>
            </div>
          </form>
        ) : (
          <form onSubmit={verify} className="space-y-4">
            <Field label={t("auth.otpLabel")} required>
              <Input required inputMode="numeric" maxLength={6} className="text-center font-mono text-lg tracking-[0.5em]" value={otpCode} onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, ""))} placeholder="••••••" />
            </Field>
            {otp?.devOtp && (
              <div className="flex items-center justify-between gap-2 rounded-md border border-dashed border-amber-300 bg-amber-50 px-2.5 py-1.5">
                <span className="text-[11px] text-amber-800">Console delivery - tap to fill</span>
                <button
                  type="button"
                  onClick={() => setOtpCode(otp!.devOtp!)}
                  className="font-mono text-sm font-bold tracking-[0.3em] text-amber-900 hover:underline"
                >
                  {otp.devOtp}
                </button>
              </div>
            )}
            <Button type="submit" className="w-full" size="lg" loading={busy}>
              {t("auth.verifyOtp")}
            </Button>
          </form>
        )}
      </div>
      </div>
    </div>
  );
}
