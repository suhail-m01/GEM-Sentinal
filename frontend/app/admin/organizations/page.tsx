"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Field, Input, Select, Textarea } from "@/components/ui/fields";
import { Loading } from "@/components/app/widgets";

interface BandRow {
  min_value: number;
  max_value: number | null;
  review_role_title: string;
  approval_role_title: string;
  committee_required?: boolean;
}

interface Org {
  id: string;
  name: string;
  short_code: string;
  org_type: string;
  parent_ministry: string | null;
  procurement_division: string | null;
  procurement_method_default: string | null;
  committee_min_members: number;
  committee_requires_finance: boolean;
  committee_requires_user_rep: boolean;
  authority_bands: BandRow[];
  active: boolean;
  tender_count?: number;
}

const ORG_TYPES = ["MINISTRY", "DEPARTMENT", "DIRECTORATE", "REGULATORY", "CPSE", "PSU", "REFINERY", "EPC"];

const DEFAULT_BANDS = `[
  { "min_value": 0, "max_value": 2500000,
    "review_role_title": "Tender Evaluation Officer",
    "approval_role_title": "Delegated Procurement Officer",
    "committee_required": false },
  { "min_value": 2500000, "max_value": 200000000,
    "review_role_title": "Tender Evaluation Committee Member",
    "approval_role_title": "Chief Procurement Officer",
    "committee_required": true },
  { "min_value": 200000000, "max_value": null,
    "review_role_title": "Tender Committee (3 members)",
    "approval_role_title": "Competent Financial Authority",
    "committee_required": true }
]`;

const EMPTY = {
  name: "",
  short_code: "",
  org_type: "CPSE",
  parent_ministry: "Ministry of Petroleum & Natural Gas",
  procurement_division: "",
  procurement_method_default: "GEM_BID",
  committee_min_members: 3,
  committee_requires_finance: true,
  committee_requires_user_rep: true,
  bands: DEFAULT_BANDS,
};

export default function AdminOrgsPage() {
  const t = useTranslations();
  const [orgs, setOrgs] = useState<Org[] | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [bandsErr, setBandsErr] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<{ organizations: Org[] }>(`/admin/organizations`).then((r) => setOrgs(r.organizations)).catch(() => setOrgs([]));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const resetForm = () => {
    setForm(EMPTY);
    setEditingId(null);
    setBandsErr("");
  };

  const edit = (o: Org) => {
    setEditingId(o.id);
    setForm({
      name: o.name,
      short_code: o.short_code,
      org_type: o.org_type,
      parent_ministry: o.parent_ministry ?? "",
      procurement_division: o.procurement_division ?? "",
      procurement_method_default: o.procurement_method_default ?? "GEM_BID",
      committee_min_members: o.committee_min_members,
      committee_requires_finance: o.committee_requires_finance,
      committee_requires_user_rep: o.committee_requires_user_rep,
      bands: JSON.stringify(o.authority_bands, null, 2),
    });
    setBandsErr("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const save = async () => {
    setBusy(true);
    setMsg(null);
    setBandsErr("");
    let bands: unknown;
    try {
      bands = JSON.parse(form.bands);
      if (!Array.isArray(bands)) throw new Error("bands must be an array");
    } catch (e) {
      setBandsErr(`Invalid authority bands JSON: ${(e as Error).message}`);
      setBusy(false);
      return;
    }
    try {
      const r = await api<{ organization: Org }>(`/admin/organizations`, {
        method: "POST",
        body: JSON.stringify({
          name: form.name,
          short_code: form.short_code,
          org_type: form.org_type,
          parent_ministry: form.parent_ministry || null,
          procurement_division: form.procurement_division || null,
          procurement_method_default: form.procurement_method_default || null,
          committee_min_members: Number(form.committee_min_members) || 3,
          committee_requires_finance: form.committee_requires_finance,
          committee_requires_user_rep: form.committee_requires_user_rep,
          authority_bands: bands,
        }),
      });
      setMsg({ ok: true, text: `${r.organization.name} saved.` });
      resetForm();
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail ?? String(e) });
    } finally {
      setBusy(false);
    }
  };

  if (!orgs) return <Loading />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("nav.orgs")}</h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">
          {t("admin.orgsDesc")}
        </p>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

      <Card>
        <CardHeader>
          <CardTitle>{editingId ? t("admin.orgEdit") : t("admin.orgCreate")}</CardTitle>
          <CardDescription>{t("admin.orgFormHint")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Name" required>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="e.g. Oil & Natural Gas Corporation Ltd" />
            </Field>
            <Field label="Short code" required>
              <Input value={form.short_code} onChange={(e) => setForm({ ...form, short_code: e.target.value.toUpperCase() })} placeholder="ONGC" />
            </Field>
            <Field label="Type">
              <Select value={form.org_type} onChange={(e) => setForm({ ...form, org_type: e.target.value })}>
                {ORG_TYPES.map((x) => <option key={x}>{x}</option>)}
              </Select>
            </Field>
            <Field label="Parent ministry">
              <Input value={form.parent_ministry} onChange={(e) => setForm({ ...form, parent_ministry: e.target.value })} />
            </Field>
            <Field label="Procurement division">
              <Input value={form.procurement_division} onChange={(e) => setForm({ ...form, procurement_division: e.target.value })} placeholder="e.g. ONGC Procurement Division, New Delhi" />
            </Field>
            <Field label="Default method">
              <Select value={form.procurement_method_default} onChange={(e) => setForm({ ...form, procurement_method_default: e.target.value })}>
                {["GEM_BID", "CUSTOM_BID", "TWO_PACKET_BID", "OPEN_TENDER", "LIMITED_TENDER"].map((x) => <option key={x}>{x}</option>)}
              </Select>
            </Field>
            <Field label="Committee min members">
              <Input type="number" min={1} max={9} value={form.committee_min_members} onChange={(e) => setForm({ ...form, committee_min_members: Number(e.target.value) })} />
            </Field>
            <div className="flex items-end gap-4 pb-1">
              <label className="flex items-center gap-1.5 text-sm">
                <input type="checkbox" checked={form.committee_requires_finance} onChange={(e) => setForm({ ...form, committee_requires_finance: e.target.checked })} />
                {t("admin.orgCommitteeFinance")}
              </label>
              <label className="flex items-center gap-1.5 text-sm">
                <input type="checkbox" checked={form.committee_requires_user_rep} onChange={(e) => setForm({ ...form, committee_requires_user_rep: e.target.checked })} />
                {t("admin.orgCommitteeUserRep")}
              </label>
            </div>
          </div>
          <Field label="Authority bands (JSON - value-driven approval chain, INR)" hint="max_value: null = no upper bound. Titles are org-specific (not fixed L1/L2/L3 labels).">
            <Textarea rows={8} className="font-mono text-xs" value={form.bands} onChange={(e) => setForm({ ...form, bands: e.target.value })} />
          </Field>
          {bandsErr && <Alert variant="danger">{bandsErr}</Alert>}
          <div className="flex gap-2">
            <Button variant="secondary" onClick={save} loading={busy} disabled={!form.name.trim() || !form.short_code.trim()}>
              {t("common.submit")}
            </Button>
            {editingId && (
              <Button variant="ghost" onClick={resetForm}>{t("common.cancel")}</Button>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("admin.orgList")}</CardTitle></CardHeader>
        <CardContent>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <th className="py-2 pr-3">Organization</th>
                <th className="py-2 pr-3">Type</th>
                <th className="py-2 pr-3">Procurement division</th>
                <th className="py-2 pr-3">Tenders</th>
                <th className="py-2">Authority bands</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {orgs.map((o) => (
                <tr key={o.id}>
                  <td className="py-2.5 pr-3">
                    <div className="font-medium text-slate-800">{o.name}</div>
                    <div className="text-xs text-slate-500">{o.short_code}{o.parent_ministry ? ` · ${o.parent_ministry}` : ""}</div>
                  </td>
                  <td className="py-2.5 pr-3"><Badge variant="slate">{o.org_type}</Badge></td>
                  <td className="py-2.5 pr-3 text-xs text-slate-600">{o.procurement_division ?? "-"}</td>
                  <td className="py-2.5 pr-3">{o.tender_count ?? 0}</td>
                  <td className="py-2.5">
                    <div className="space-y-0.5">
                      {(o.authority_bands ?? []).map((b, i) => (
                        <div key={i} className="text-[11px] text-slate-500">
                          {Number(b.min_value).toLocaleString()}–{(b.max_value != null ? Number(b.max_value).toLocaleString() : "∞")}: {b.review_role_title} → {b.approval_role_title}
                          {b.committee_required ? " · committee" : ""}
                        </div>
                      ))}
                    </div>
                    <Button size="sm" variant="ghost" className="mt-1" onClick={() => edit(o)}>
                      {t("admin.orgEdit")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {orgs.length === 0 && <p className="py-4 text-sm text-slate-500">{t("common.noData")}</p>}
        </CardContent>
      </Card>
    </div>
  );
}
