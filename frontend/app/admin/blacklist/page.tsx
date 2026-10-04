"use client";

import React, { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/fields";
import { Table, TBody, TH, TD, THead, TR } from "@/components/ui/table";
import { Loading } from "@/components/app/widgets";
import { fmtDate } from "@/lib/utils";

interface Entry {
  id: string;
  entity_name: string;
  pan_masked: string | null;
  gstin_masked: string | null;
  reference_id: string | null;
  reason: string;
  source_authority: string;
  official_order_reference: string | null;
  valid_from: string | null;
  valid_until: string | null;
  status: string;
  review_status: string | null;
  version: number;
  created_at: string;
}

export default function BlacklistPage() {
  const t = useTranslations();
  const [label, setLabel] = useState("");
  const [rows, setRows] = useState<Entry[] | null>(null);
  const [status, setStatus] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [form, setForm] = useState({
    entity_name: "",
    pan: "",
    gstin: "",
    reference_id: "",
    reason: "",
    source_authority: "",
    official_order_reference: "",
    valid_from: "",
    valid_until: "",
  });
  const [busy, setBusy] = useState(false);

  const load = () =>
    api<{ label: string; entries: Entry[] }>(`/admin/blacklist${status ? `?status=${status}` : ""}`).then((d) => {
      setLabel(d.label);
      setRows(d.entries);
    });
  useEffect(() => {
    load();
  }, [status]);

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api(`/admin/blacklist`, {
        method: "POST",
        body: JSON.stringify({
          ...form,
          valid_from: form.valid_from || undefined,
          valid_until: form.valid_until || undefined,
        }),
      });
      setMsg({ ok: true, text: "Entry added to Reference Blacklist Database." });
      setAddOpen(false);
      setForm({ entity_name: "", pan: "", gstin: "", reference_id: "", reason: "", source_authority: "", official_order_reference: "", valid_from: "", valid_until: "" });
      load();
    } catch (e2: any) {
      setMsg({ ok: false, text: e2?.detail ?? String(e2) });
    } finally {
      setBusy(false);
    }
  };

  const changeStatus = async (id: string, s: string) => {
    setMsg(null);
    try {
      const r = await api<{ message: string }>(`/admin/blacklist/${id}/status`, { method: "POST", body: JSON.stringify({ status: s }) });
      setMsg({ ok: true, text: r.message });
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail ?? String(e) });
    }
  };

  if (!rows) return <Loading />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">{t("nav.blacklist")}</h1>
          <p className="text-sm text-slate-500">{label}</p>
        </div>
        <Button variant="secondary" onClick={() => setAddOpen(true)}>+ {t("admin.addEntry")}</Button>
      </div>

      <Alert variant="info">{t("admin.blacklistHint")}</Alert>
      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

      <Card className="p-4">
        <div className="flex gap-3">
          <Select className="max-w-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Status: All</option>
            {["ACTIVE", "EXPIRED", "APPEAL_PENDING", "REMOVED"].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
        </div>
      </Card>

      <Table>
        <THead>
          <TR>
            <TH>Entity</TH>
            <TH>Identifiers</TH>
            <TH>{t("common.reason")}</TH>
            <TH>{t("admin.sourceAuthority")}</TH>
            <TH>Valid</TH>
            <TH>{t("common.status")}</TH>
            <TH>{t("common.actions")}</TH>
          </TR>
        </THead>
        <TBody>
          {rows.map((e) => (
            <TR key={e.id}>
              <TD className="font-medium text-slate-900">
                {e.entity_name}
                <div className="text-xs text-slate-500">v{e.version} · added {fmtDate(e.created_at)}</div>
              </TD>
              <TD className="text-xs">
                {e.pan_masked && <div>PAN: {e.pan_masked}</div>}
                {e.gstin_masked && <div>GSTIN: {e.gstin_masked}</div>}
                {e.reference_id && <div className="text-slate-500">Ref: {e.reference_id}</div>}
              </TD>
              <TD className="max-w-[220px] text-xs">{e.reason}</TD>
              <TD className="text-xs">{e.source_authority}{e.official_order_reference ? <div className="text-slate-500">{e.official_order_reference}</div> : null}</TD>
              <TD className="text-xs">
                {e.valid_from ? fmtDate(e.valid_from) : "-"} → {e.valid_until ? fmtDate(e.valid_until) : "∞"}
              </TD>
              <TD>
                <Badge variant={e.status === "ACTIVE" ? "critical" : e.status === "EXPIRED" ? "slate" : e.status === "APPEAL_PENDING" ? "warning" : "success"}>{e.status}</Badge>
              </TD>
              <TD>
                <div className="flex flex-wrap gap-1">
                  {e.status !== "ACTIVE" && <Button size="sm" variant="outline" onClick={() => changeStatus(e.id, "ACTIVE")}>Activate</Button>}
                  {e.status !== "EXPIRED" && <Button size="sm" variant="ghost" onClick={() => changeStatus(e.id, "EXPIRED")}>Expire</Button>}
                  {e.status !== "APPEAL_PENDING" && <Button size="sm" variant="ghost" onClick={() => changeStatus(e.id, "APPEAL_PENDING")}>Appeal pending</Button>}
                  {e.status !== "REMOVED" && <Button size="sm" variant="ghost" onClick={() => changeStatus(e.id, "REMOVED")}>Remove (status)</Button>}
                </div>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>

      <Dialog open={addOpen} onClose={() => setAddOpen(false)} title={t("admin.addEntry")} wide>
        <form onSubmit={add} className="grid gap-4 sm:grid-cols-2">
          <Field label={t("admin.entityName")} required>
            <Input required value={form.entity_name} onChange={set("entity_name")} />
          </Field>
          <Field label="PAN (optional)">
            <Input value={form.pan} onChange={set("pan")} maxLength={10} />
          </Field>
          <Field label="GSTIN (optional)">
            <Input value={form.gstin} onChange={set("gstin")} maxLength={15} />
          </Field>
          <Field label="Reference ID">
            <Input value={form.reference_id} onChange={set("reference_id")} />
          </Field>
          <Field label={t("common.reason")} required className="sm:col-span-2">
            <Input required value={form.reason} onChange={set("reason")} />
          </Field>
          <Field label={t("admin.sourceAuthority")} required>
            <Input required value={form.source_authority} onChange={set("source_authority")} placeholder="e.g. CVC / DCGM / Ministry order" />
          </Field>
          <Field label={t("admin.orderRef")}>
            <Input value={form.official_order_reference} onChange={set("official_order_reference")} />
          </Field>
          <Field label={t("admin.validFrom")}>
            <Input type="date" value={form.valid_from} onChange={set("valid_from")} />
          </Field>
          <Field label={t("admin.validUntil")}>
            <Input type="date" value={form.valid_until} onChange={set("valid_until")} />
          </Field>
          <div className="flex justify-end gap-2 sm:col-span-2">
            <Button type="button" variant="ghost" onClick={() => setAddOpen(false)}>{t("common.cancel")}</Button>
            <Button type="submit" loading={busy}>{t("common.save")}</Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
