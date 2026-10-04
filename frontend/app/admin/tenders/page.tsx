"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Landmark, Lock, Unlock } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/fields";
import { Table, TBody, TH, TD, THead, TR } from "@/components/ui/table";
import { Loading } from "@/components/app/widgets";
import { fmtDate, formatINR } from "@/lib/utils";

interface TenderRow {
  id: string;
  tender_no: string;
  title: string;
  department: string;
  district: string;
  state: string;
  status: string;
  close_date: string;
  value_max: number | null;
  rules_count: number;
  rules_version: number;
  bid_deadline: string | null;
  effective_bid_deadline: string | null;
  bids_open: boolean;
}

export default function AdminTendersPage() {
  const t = useTranslations();
  const [rows, setRows] = useState<TenderRow[] | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    title: "",
    department: "",
    district: "",
    state: "Karnataka",
    jurisdiction_code: "L1_BELAGAVI",
    category: "IT Hardware",
    close_date: "",
    value_min: "",
    value_max: "",
    msme_preference: false,
    make_in_india_required: false,
    startup_eligible: false,
    description: "",
  });

  const load = () => api<{ tenders: TenderRow[] }>(`/admin/tenders`).then((d) => setRows(d.tenders));
  useEffect(() => {
    load();
  }, []);

  const forceOpen = async (id: string, tenderNo: string) => {
    if (!window.confirm(`${t("admin.forceOpenConfirm")}\n${tenderNo}`)) return;
    setMsg(null);
    try {
      const r = await api<{ message: string }>(`/admin/tenders/${id}/open-bids`, { method: "POST" });
      setMsg({ ok: true, text: r.message });
      load();
    } catch (e2: any) {
      setMsg({ ok: false, text: e2?.detail ?? String(e2) });
    }
  };

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value });

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api(`/admin/tenders`, {
        method: "POST",
        body: JSON.stringify({
          ...form,
          value_min: form.value_min ? Number(form.value_min) : null,
          value_max: form.value_max ? Number(form.value_max) : null,
          required_document_types: ["UDYAM_CERT", "GST_CERT", "PAN"],
        }),
      });
      setMsg({ ok: true, text: "Tender created (DRAFT). Open it to configure rules, then publish." });
      setCreateOpen(false);
      load();
    } catch (e2: any) {
      setMsg({ ok: false, text: e2?.detail ?? String(e2) });
    } finally {
      setBusy(false);
    }
  };

  if (!rows) return <Loading />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">{t("nav.tenderMgmt")}</h1>
          <p className="text-sm text-slate-500">Create tenders, configure deterministic rules, publish & close</p>
        </div>
        <Button variant="secondary" onClick={() => setCreateOpen(true)}>+ {t("admin.createTender")}</Button>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

      <Table>
        <THead>
          <TR>
            <TH>Tender</TH>
            <TH>{t("common.district")}</TH>
            <TH>{t("common.value")}</TH>
            <TH>Close</TH>
            <TH>{t("admin.bidDeadline")}</TH>
            <TH>Rules</TH>
            <TH>{t("common.status")}</TH>
            <TH>{t("common.actions")}</TH>
          </TR>
        </THead>
        <TBody>
          {rows.map((r) => (
            <TR key={r.id}>
              <TD>
                <div className="flex items-center gap-2">
                  <Landmark size={15} className="text-slate-400" />
                  <div>
                    <div className="font-medium text-navy-900">{r.tender_no}</div>
                    <div className="text-xs text-slate-500">{r.title}</div>
                  </div>
                </div>
              </TD>
              <TD className="text-slate-600">{r.district}</TD>
              <TD>{r.value_max ? formatINR(r.value_max) : "-"}</TD>
              <TD className="text-xs">{fmtDate(r.close_date)}</TD>
              <TD className="text-xs">
                {r.effective_bid_deadline ? (
                  <div className="flex flex-col gap-0.5">
                    <span>{fmtDate(r.effective_bid_deadline)} 18:00 IST</span>
                    <Badge variant={r.bids_open ? "success" : "warning"}>
                      {r.bids_open ? <Unlock size={10} className="mr-1 inline" /> : <Lock size={10} className="mr-1 inline" />}
                      {r.bids_open ? t("admin.bidsOpen") : t("admin.bidsSealed")}
                    </Badge>
                  </div>
                ) : (
                  "-"
                )}
              </TD>
              <TD>
                <Badge variant="slate">{r.rules_count} rules · v{r.rules_version}</Badge>
              </TD>
              <TD>
                <Badge variant={r.status === "PUBLISHED" ? "success" : r.status === "CLOSED" ? "slate" : "warning"}>{r.status}</Badge>
              </TD>
              <TD>
                <div className="flex flex-col items-start gap-1">
                  <Link href={`/admin/tenders/${r.id}`} className="text-sm font-medium text-navy-700 hover:underline">{t("common.view")} →</Link>
                  {r.effective_bid_deadline && !r.bids_open && (
                    <Button size="sm" variant="outline" onClick={() => forceOpen(r.id, r.tender_no)}>
                      <Unlock size={12} /> {t("admin.forceOpen")}
                    </Button>
                  )}
                </div>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>

      <Dialog open={createOpen} onClose={() => setCreateOpen(false)} title={t("admin.createTender")} wide>
        <form onSubmit={create} className="grid gap-4 sm:grid-cols-2">
          <Field label={t("common.details") + " (title)"} required className="sm:col-span-2">
            <Input required value={form.title} onChange={set("title")} />
          </Field>
          <Field label={t("admin.department")} required>
            <Input required value={form.department} onChange={set("department")} placeholder="Dept. of IT & BS" />
          </Field>
          <Field label={t("common.category")}>
            <Select value={form.category} onChange={set("category")}>
              {["IT Hardware", "Stationery", "Civil Works", "Furniture", "Electrical", "Medical"].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </Select>
          </Field>
          <Field label={t("common.district")} required>
            <Input required value={form.district} onChange={set("district")} />
          </Field>
          <Field label={t("common.state")} required>
            <Input required value={form.state} onChange={set("state")} />
          </Field>
          <Field label={t("admin.jurisdictionCode")} required>
            <Input required value={form.jurisdiction_code} onChange={set("jurisdiction_code")} />
          </Field>
          <Field label={t("common.closeDate")} required>
            <Input required type="date" value={form.close_date} onChange={set("close_date")} />
          </Field>
          <Field label="Value min (INR)">
            <Input type="number" value={form.value_min} onChange={set("value_min")} />
          </Field>
          <Field label="Value max (INR)">
            <Input type="number" value={form.value_max} onChange={set("value_max")} />
          </Field>
          <div className="sm:col-span-2 flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.msme_preference} onChange={(e) => setForm({ ...form, msme_preference: e.target.checked })} />
              MSME preference
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.make_in_india_required} onChange={(e) => setForm({ ...form, make_in_india_required: e.target.checked })} />
              Make in India required
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.startup_eligible} onChange={(e) => setForm({ ...form, startup_eligible: e.target.checked })} />
              Startup eligible
            </label>
          </div>
          <Field label="Description" className="sm:col-span-2">
            <Textarea value={form.description} onChange={set("description")} />
          </Field>
          <div className="flex justify-end gap-2 sm:col-span-2">
            <Button type="button" variant="ghost" onClick={() => setCreateOpen(false)}>{t("common.cancel")}</Button>
            <Button type="submit" loading={busy}>{t("admin.createTender")}</Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
