"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { IdCard, Plus, Search, UserCheck, UserX } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/fields";
import { Table, TBody, TH, TD, THead, TR } from "@/components/ui/table";
import { Loading } from "@/components/app/widgets";
import { fmtDate } from "@/lib/utils";
import { RoleBadge } from "@/components/app/shell";

const GOV_ROLES = [
  "ADMIN", "AUDITOR", "PROCUREMENT_OFFICER", "BID_OPENING_OFFICER",
  "TECHNICAL_EVALUATOR", "FINANCE_EVALUATOR", "COMMITTEE_CHAIR", "COMMITTEE_MEMBER",
  "CFA", "CONTRACT_MANAGER", "CONTRACT_OFFICER", "INSPECTION_OFFICER",
  "PAYMENT_OFFICER", "INDENTOR", "L1_OFFICER", "L2_OFFICER",
];

interface GovEmp {
  id: string;
  gov_id: string;
  full_name: string;
  phone_masked: string;
  role: string;
  department: string | null;
  status: "PENDING_APPROVAL" | "ACTIVE" | "SUSPENDED";
  user_id: string | null;
  user_email: string | null;
  approved_at: string | null;
  last_login_at: string | null;
}

const STATUS_CLS: Record<string, string> = {
  PENDING_APPROVAL: "bg-amber-100 text-amber-800",
  ACTIVE: "bg-emerald-100 text-emerald-800",
  SUSPENDED: "bg-rose-100 text-rose-700",
};

export default function AdminGovEmployeesPage() {
  const t = useTranslations();
  const [rows, setRows] = useState<GovEmp[] | null>(null);
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ gov_id: "", full_name: "", phone: "", role: "PROCUREMENT_OFFICER", department: "" });

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (role) params.set("role", role);
    const d = await api<{ employees: GovEmp[] }>(`/admin/gov-employees?${params.toString()}`);
    setRows(d.employees);
  }, [q, role]);

  useEffect(() => {
    const id = setTimeout(load, q ? 250 : 0);
    return () => clearTimeout(id);
  }, [load, q]);

  const act = async (emp: GovEmp, action: "approve" | "suspend") => {
    try {
      const r = await api<{ message: string }>(`/admin/gov-employees/${emp.id}/${action}`, { method: "POST" });
      setMsg({ ok: true, text: r.message });
      await load();
    } catch (e: any) {
      const det = e?.detail;
      setMsg({ ok: false, text: typeof det === "string" ? det : String(e) });
    }
  };

  const submitCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const r = await api<{ message: string }>("/admin/gov-employees", {
        method: "POST",
        body: JSON.stringify({
          gov_id: form.gov_id,
          full_name: form.full_name,
          phone: form.phone,
          role: form.role,
          department: form.department || null,
        }),
      });
      setMsg({ ok: true, text: `${r.message} (${t("admin.employeeCreated")})` });
      setCreating(false);
      setForm({ gov_id: "", full_name: "", phone: "", role: "PROCUREMENT_OFFICER", department: "" });
      await load();
    } catch (e: any) {
      const det = e?.detail;
      setMsg({ ok: false, text: typeof det === "string" ? det : String(e) });
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-sky-100 text-sky-700">
              <IdCard size={18} />
            </div>
            <div>
              <CardTitle>{t("admin.govEmployees")}</CardTitle>
              <p className="text-xs text-slate-500">{t("admin.govEmployeesDesc")}</p>
            </div>
          </div>
          <Button size="sm" onClick={() => setCreating(true)}>
            <Plus size={14} className="mr-1" /> {t("admin.addEmployee")}
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input
                className="w-64 pl-8"
                placeholder={t("admin.searchPlaceholder")}
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
            <Select value={role} onChange={(e) => setRole(e.target.value)} className="w-56">
              <option value="">{t("admin.role")}: all</option>
              {GOV_ROLES.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </Select>
          </div>

          {rows === null ? (
            <Loading />
          ) : rows.length === 0 ? (
            <p className="py-6 text-center text-sm text-slate-400">{t("admin.noResults")}</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-slate-200">
              <Table>
                <THead>
                  <TR>
                    <TH>{t("admin.govtId")}</TH>
                    <TH>{t("admin.fullName")}</TH>
                    <TH>{t("admin.registeredPhone")}</TH>
                    <TH>{t("admin.role")}</TH>
                    <TH>{t("admin.department")}</TH>
                    <TH>{t("admin.status")}</TH>
                    <TH>{t("admin.linkedUser")}</TH>
                    <TH>{t("admin.lastLogin")}</TH>
                    <TH className="text-right">Actions</TH>
                  </TR>
                </THead>
                <TBody>
                  {rows.map((e) => (
                    <TR key={e.id}>
                      <TD className="font-mono text-xs font-bold text-navy-800">{e.gov_id}</TD>
                      <TD>
                        <div className="text-sm font-medium text-slate-800">{e.full_name}</div>
                        {e.approved_at && (
                          <div className="text-[10px] text-slate-400">{t("admin.created")}: {fmtDate(e.approved_at)}</div>
                        )}
                      </TD>
                      <TD className="font-mono text-xs">{e.phone_masked}</TD>
                      <TD><RoleBadge role={e.role as never} /></TD>
                      <TD className="text-xs text-slate-600">{e.department ?? "-"}</TD>
                      <TD>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${STATUS_CLS[e.status] ?? "bg-slate-100 text-slate-600"}`}>
                          {e.status === "PENDING_APPROVAL" ? t("admin.pendingApproval") : e.status}
                        </span>
                      </TD>
                      <TD className="max-w-[180px] truncate text-xs text-slate-500" title={e.user_email ?? ""}>
                        {e.user_email ?? "-"}
                      </TD>
                      <TD className="text-xs text-slate-500">{e.last_login_at ? fmtDate(e.last_login_at) : "-"}</TD>
                      <TD className="text-right">
                        <div className="flex justify-end gap-1.5">
                          {e.status !== "ACTIVE" && (
                            <Button size="sm" variant="secondary" onClick={() => act(e, "approve")}>
                              <UserCheck size={13} className="mr-1" /> {t("admin.approve")}
                            </Button>
                          )}
                          {e.status !== "SUSPENDED" && (
                            <Button size="sm" variant="destructive" onClick={() => act(e, "suspend")}>
                              <UserX size={13} className="mr-1" /> {t("admin.suspend")}
                            </Button>
                          )}
                        </div>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}

          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Badge variant="slate">{rows?.length ?? 0}</Badge>
            <span>OTP destination for every Govt ID is the registered phone (E.164) - shown masked. Officer sign-in: Govt ID + OTP to that phone; first login needs approval.</span>
          </div>
        </CardContent>
      </Card>

      <Dialog open={creating} onClose={() => setCreating(false)} title={t("admin.createEmployee")}>
        <form onSubmit={submitCreate} className="grid gap-4 sm:grid-cols-2">
          <Field label={t("admin.govtId")} required>
            <Input
              required
              placeholder="GOI-PNG-2026-1203"
              className="font-mono tracking-widest"
              value={form.gov_id}
              onChange={(e) => setForm({ ...form, gov_id: e.target.value.toUpperCase() })}
            />
          </Field>
          <Field label={t("admin.fullName")} required>
            <Input required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />
          </Field>
          <Field label={t("admin.registeredPhone")} required hint={t("admin.phoneHelp")}>
            <Input
              required
              placeholder="+919812345678"
              className="font-mono"
              value={form.phone}
              onChange={(e) => setForm({ ...form, phone: e.target.value })}
            />
          </Field>
          <Field label={t("admin.role")} required>
            <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {GOV_ROLES.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </Select>
          </Field>
          <Field label={t("admin.department")}>
            <Input value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })} />
          </Field>
          <div className="flex items-end justify-end gap-2 sm:col-span-2">
            <Button type="button" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
            <Button type="submit">{t("admin.addEmployee")}</Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
