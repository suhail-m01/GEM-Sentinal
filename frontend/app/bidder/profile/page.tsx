"use client";

import React, { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/fields";
import { Alert } from "@/components/ui/alert";
import { Loading } from "@/components/app/widgets";
import { formatINR } from "@/lib/utils";

interface Profile {
  company_name: string | null;
  trade_name: string | null;
  pan_masked: string | null;
  gstin_masked: string | null;
  udyam_masked: string | null;
  business_type: string | null;
  business_category: string | null;
  registered_address: string | null;
  city: string | null;
  district: string | null;
  state: string | null;
  pincode: string | null;
  authorized_rep_name: string | null;
  authorized_rep_email: string | null;
  authorized_rep_phone: string | null;
  annual_turnover: number | null;
  established_year: number | null;
  registration_date: string | null;
  filing_status: string | null;
  local_content_percent: number | null;
  profile_completion: number;
}

export default function BidderProfilePage() {
  const t = useTranslations();
  const [p, setP] = useState<Profile | null>(null);
  const [form, setForm] = useState<Record<string, any>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Profile>(`/bidder/profile`).then((d) => {
      setP(d);
      setForm({
        company_name: d.company_name ?? "",
        trade_name: d.trade_name ?? "",
        business_type: d.business_type ?? "",
        business_category: d.business_category ?? "",
        registered_address: d.registered_address ?? "",
        city: d.city ?? "",
        district: d.district ?? "",
        state: d.state ?? "",
        pincode: d.pincode ?? "",
        authorized_rep_name: d.authorized_rep_name ?? "",
        authorized_rep_email: d.authorized_rep_email ?? "",
        authorized_rep_phone: d.authorized_rep_phone ?? "",
        annual_turnover: d.annual_turnover ?? "",
        established_year: d.established_year ?? "",
        filing_status: d.filing_status ?? "",
        local_content_percent: d.local_content_percent ?? "",
      });
    });
  }, []);

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const body: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(form)) {
        if (v === "" || v === null) continue;
        body[k] = ["annual_turnover", "established_year", "local_content_percent"].includes(k) ? Number(v) : v;
      }
      await api(`/bidder/profile`, { method: "PUT", body: JSON.stringify(body) });
      setMsg({ ok: true, text: t("common.save") + " ✓" });
    } catch (err: any) {
      setMsg({ ok: false, text: err?.detail ?? String(err) });
    } finally {
      setBusy(false);
    }
  };

  if (!p) return <Loading />;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-navy-900">{t("nav.profile")}</h1>
        <div className="text-sm text-slate-600">
          {t("bidder.profileCompletion")}: <b className={p.profile_completion >= 80 ? "text-emerald-600" : "text-amber-600"}>{p.profile_completion}%</b>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Identity & tax identifiers</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm">
          <div className="grid gap-2 sm:grid-cols-3">
            <div><span className="label">PAN</span><div className="font-mono">{p.pan_masked || "-"}</div></div>
            <div><span className="label">GSTIN</span><div className="font-mono">{p.gstin_masked || "-"}</div></div>
            <div><span className="label">Udyam</span><div className="font-mono">{p.udyam_masked || "-"}</div></div>
          </div>
          <Alert variant="info">
            PAN / GSTIN / Udyam numbers are stored encrypted (Fernet) and shown masked. Update them in the form below.
          </Alert>
        </CardContent>
      </Card>

      <form onSubmit={save}>
        <Card>
          <CardHeader>
            <CardTitle>{t("common.profile")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            {msg && <Alert variant={msg.ok ? "success" : "danger"} className="sm:col-span-2">{msg.text}</Alert>}
            <Field label={t("auth.company")}>
              <Input value={form.company_name} onChange={set("company_name")} />
            </Field>
            <Field label="Trade name">
              <Input value={form.trade_name} onChange={set("trade_name")} />
            </Field>
            <Field label={t("auth.pan")} hint="Stored encrypted">
              <Input value={form.pan ?? ""} onChange={set("pan")} placeholder="ABCDE1234F" />
            </Field>
            <Field label={t("auth.gstin")} hint="Stored encrypted">
              <Input value={form.gstin ?? ""} onChange={set("gstin")} placeholder="29ABCDE1234F1Z5" />
            </Field>
            <Field label={t("auth.udyam")} hint="Stored encrypted">
              <Input value={form.udyam_number ?? ""} onChange={set("udyam_number")} />
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
            <Field label={t("common.category")}>
              <Select value={form.business_category} onChange={set("business_category")}>
                <option value="">-</option>
                <option>Manufacturing</option>
                <option>Trading</option>
                <option>Services</option>
                <option>Construction</option>
                <option>Wholesale</option>
              </Select>
            </Field>
            <Field label={t("auth.address")} className="sm:col-span-2">
              <Input value={form.registered_address} onChange={set("registered_address")} />
            </Field>
            <Field label="City">
              <Input value={form.city} onChange={set("city")} />
            </Field>
            <Field label={t("common.district")}>
              <Input value={form.district} onChange={set("district")} />
            </Field>
            <Field label={t("common.state")}>
              <Input value={form.state} onChange={set("state")} />
            </Field>
            <Field label="Pincode">
              <Input value={form.pincode} onChange={set("pincode")} />
            </Field>
            <Field label="Authorized representative">
              <Input value={form.authorized_rep_name} onChange={set("authorized_rep_name")} />
            </Field>
            <Field label="Rep email">
              <Input type="email" value={form.authorized_rep_email} onChange={set("authorized_rep_email")} />
            </Field>
            <Field label="Rep phone">
              <Input value={form.authorized_rep_phone} onChange={set("authorized_rep_phone")} />
            </Field>
            <Field label="Annual turnover (INR)">
              <Input type="number" value={form.annual_turnover} onChange={set("annual_turnover")} />
            </Field>
            <Field label="Established year">
              <Input type="number" value={form.established_year} onChange={set("established_year")} />
            </Field>
            <Field label="Filing status (ITR / GST returns)">
              <Select value={form.filing_status} onChange={set("filing_status")}>
                <option value="">-</option>
                <option>UP_TO_DATE</option>
                <option>PENDING</option>
                <option>LAPSED</option>
              </Select>
            </Field>
            <Field label="Local content %" hint="For Make in India requirements">
              <Input type="number" value={form.local_content_percent} onChange={set("local_content_percent")} />
            </Field>
            <div className="sm:col-span-2">
              <Button type="submit" loading={busy}>{t("common.save")}</Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}
