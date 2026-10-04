"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Search } from "lucide-react";
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

interface Jurisdiction {
  id: string;
  code: string;
  district: string | null;
  state: string | null;
  department: string | null;
  organization: string | null;
  description: string | null;
}

interface UserRow {
  id: string;
  email: string;
  full_name: string;
  mobile: string | null;
  role: string;
  status: string;
  is_verified: boolean;
  company: string | null;
  district: string | null;
  jurisdictions: Jurisdiction[];
  last_login_at: string | null;
  created_at: string;
}

export default function AdminUsersPage() {
  const t = useTranslations();
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [juris, setJuris] = useState<Jurisdiction[]>([]);
  const [role, setRole] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [approveUser, setApproveUser] = useState<UserRow | null>(null);
  const [approveRole, setApproveRole] = useState("PROCUREMENT_OFFICER");

  const ALL_ROLES = [
    "BIDDER",
    "BID_PREPARER",
    "SIGNATORY",
    "INDENTOR",
    "PROCUREMENT_OFFICER",
    "BID_OPENING_OFFICER",
    "TECHNICAL_EVALUATOR",
    "FINANCE_EVALUATOR",
    "COMMITTEE_CHAIR",
    "COMMITTEE_MEMBER",
    "CFA",
    "CONTRACT_MANAGER",
    "CONTRACT_OFFICER",
    "INSPECTION_OFFICER",
    "PAYMENT_OFFICER",
    "AUDITOR",
    "ADMIN",
    "L1_OFFICER",
    "L2_OFFICER",
  ];

  const ROLE_LABELS: Record<string, string> = {
    BIDDER: "Bidder / Client",
    BID_PREPARER: "Bid Preparer",
    SIGNATORY: "Bid Signatory",
    INDENTOR: "User Dept. Representative",
    PROCUREMENT_OFFICER: "Procurement Officer",
    BID_OPENING_OFFICER: "Bid Opening Officer",
    TECHNICAL_EVALUATOR: "Technical Evaluator",
    FINANCE_EVALUATOR: "Finance Evaluator",
    COMMITTEE_CHAIR: "Tender Committee Chair",
    COMMITTEE_MEMBER: "Tender Committee Member",
    CFA: "Competent Financial Authority",
    CONTRACT_MANAGER: "Contract Manager",
    CONTRACT_OFFICER: "Contract Execution Officer",
    INSPECTION_OFFICER: "Inspection & Delivery Officer",
    PAYMENT_OFFICER: "Payment & Settlement Officer",
    AUDITOR: "Audit & Records (Read-only)",
    ADMIN: "System Administrator",
    L1_OFFICER: "L1 Officer (legacy)",
    L2_OFFICER: "L2 Officer (legacy)",
  };

  // Roles an admin may assign when approving a pending officer account.
  const ASSIGNABLE_ROLES = [
    "PROCUREMENT_OFFICER",
    "BID_OPENING_OFFICER",
    "TECHNICAL_EVALUATOR",
    "FINANCE_EVALUATOR",
    "COMMITTEE_CHAIR",
    "COMMITTEE_MEMBER",
    "CFA",
    "CONTRACT_MANAGER",
    "CONTRACT_OFFICER",
    "INSPECTION_OFFICER",
    "PAYMENT_OFFICER",
    "AUDITOR",
  ];
  const [approveJuris, setApproveJuris] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [newJuris, setNewJuris] = useState({ code: "", district: "", state: "", department: "", organization: "CPCL" });
  const [jurisOpen, setJurisOpen] = useState(false);

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (role) params.set("role", role);
    if (status) params.set("status", status);
    if (q) params.set("q", q);
    api<{ users: UserRow[] }>(`/admin/users?${params.toString()}`).then((d) => setUsers(d.users));
  }, [role, status, q]);

  useEffect(() => {
    load();
    api<{ jurisdictions: Jurisdiction[] }>(`/admin/jurisdictions`).then((d) => setJuris(d.jurisdictions)).catch(() => {});
  }, [load]);

  const act = async (u: UserRow, path: string) => {
    try {
      const r = await api<any>(`/admin/users/${u.id}${path}`, { method: "POST" });
      setMsg({ ok: true, text: r?.message ?? "Done" });
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail ?? String(e) });
    }
  };

  // Jurisdiction assignment is mandatory for queue-based officer roles
  // (legacy routing selects them by jurisdiction); other roles are tender-scoped.
  const NEEDS_JURISDICTION = new Set([
    "PROCUREMENT_OFFICER",
    "BID_OPENING_OFFICER",
    "TECHNICAL_EVALUATOR",
    "FINANCE_EVALUATOR",
    "L1_OFFICER",
    "L2_OFFICER",
  ]);

  const approve = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!approveUser) return;
    if (NEEDS_JURISDICTION.has(approveRole) && approveJuris.length === 0) {
      setMsg({ ok: false, text: "Select at least one jurisdiction." });
      return;
    }
    setBusy(true);
    try {
      const r = await api<{ message: string }>(`/admin/users/${approveUser.id}/approve`, {
        method: "POST",
        body: JSON.stringify({ role: approveRole, jurisdiction_ids: approveJuris }),
      });
      setMsg({ ok: true, text: r.message });
      setApproveUser(null);
      setApproveJuris([]);
      load();
    } catch (e2: any) {
      setMsg({ ok: false, text: e2?.detail ?? String(e2) });
    } finally {
      setBusy(false);
    }
  };

  const createJurisdiction = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api(`/admin/jurisdictions`, { method: "POST", body: JSON.stringify(newJuris) });
      setMsg({ ok: true, text: `Jurisdiction ${newJuris.code} created.` });
      setJurisOpen(false);
      setNewJuris({ code: "", district: "", state: "", department: "", organization: "CPCL" });
      api<{ jurisdictions: Jurisdiction[] }>(`/admin/jurisdictions`).then((d) => setJuris(d.jurisdictions));
    } catch (e2: any) {
      setMsg({ ok: false, text: e2?.detail ?? String(e2) });
    } finally {
      setBusy(false);
    }
  };

  if (!users) return <Loading />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">{t("admin.reviewUsers")}</h1>
          <p className="text-sm text-slate-500">Users, officer approval & jurisdiction management</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => setJurisOpen(true)}>+ {t("admin.createJurisdiction")}</Button>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

      <Card className="p-4">
        <div className="grid gap-3 md:grid-cols-4">
          <div className="relative">
            <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
            <Input className="pl-9" placeholder="Name or email…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <Select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="">Role: All</option>
            {ALL_ROLES.map((r) => (
              <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>
            ))}
          </Select>
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Status: All</option>
            {["ACTIVE", "PENDING_APPROVAL", "PENDING_OTP", "SUSPENDED"].map((s) => (
              <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
            ))}
          </Select>
          <button className="justify-self-end rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50" onClick={() => { setRole(""); setStatus(""); setQ(""); }}>
            {t("common.reset")}
          </button>
        </div>
      </Card>

      <Table>
        <THead>
          <TR>
            <TH>User</TH>
            <TH>Role</TH>
            <TH>{t("common.status")}</TH>
            <TH>{t("admin.assignJurisdiction")}</TH>
            <TH>{t("common.date")}</TH>
            <TH>{t("common.actions")}</TH>
          </TR>
        </THead>
        <TBody>
          {users.map((u) => (
            <TR key={u.id}>
              <TD>
                <div className="font-medium text-slate-900">{u.full_name}</div>
                <div className="text-xs text-slate-500">{u.email}{u.company ? ` · ${u.company}` : ""}</div>
              </TD>
              <TD><RoleBadge role={u.role as any} /></TD>
              <TD>
                <Badge variant={u.status === "ACTIVE" ? "success" : u.status === "SUSPENDED" ? "danger" : "warning"}>
                  {u.status.replace(/_/g, " ")}
                </Badge>
                {u.is_verified && <span className="ml-1 text-xs text-emerald-600" title="Verified">✓</span>}
              </TD>
              <TD>
                <div className="flex flex-wrap gap-1">
                  {u.jurisdictions.map((j) => (
                    <Badge key={j.id} variant="slate">{j.code}</Badge>
                  ))}
                  {u.jurisdictions.length === 0 && <span className="text-xs text-slate-400">-</span>}
                </div>
              </TD>
              <TD className="text-xs">{u.created_at ? fmtDate(u.created_at) : "-"}</TD>
              <TD>
                <div className="flex flex-wrap gap-1.5">
                  {u.status === "PENDING_APPROVAL" && (
                    <Button size="sm" variant="secondary" onClick={() => { setApproveUser(u); setApproveJuris(u.jurisdictions[0] ? [u.jurisdictions[0].id] : []); }}>
                      {t("admin.approve")}
                    </Button>
                  )}
                  {u.status === "ACTIVE" && (
                    <Button size="sm" variant="outline" onClick={() => act(u, "/suspend")}>{t("admin.suspend")}</Button>
                  )}
                  {(u.status === "SUSPENDED" || u.status === "PENDING_APPROVAL") && u.status !== "PENDING_APPROVAL" && (
                    <Button size="sm" variant="ghost" onClick={() => act(u, "/activate")}>{t("admin.activate")}</Button>
                  )}
                </div>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>

      {/* Approve officer dialog */}
      <Dialog open={!!approveUser} onClose={() => setApproveUser(null)} title={`${t("admin.approve")} - ${approveUser?.full_name ?? ""}`}>
        <form onSubmit={approve} className="space-y-4">
          <Field label={t("admin.assignRole")} required>
            <Select value={approveRole} onChange={(e) => setApproveRole(e.target.value)}>
              {ASSIGNABLE_ROLES.map((r) => (
                <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>
              ))}
            </Select>
          </Field>
          <Field
            label={`${t("admin.assignJurisdiction")}${NEEDS_JURISDICTION.has(approveRole) ? "" : " (optional)"}`}
            required={NEEDS_JURISDICTION.has(approveRole)}
          >
            <div className="space-y-1.5 rounded-md border border-slate-200 p-3">
              {juris.map((j) => (
                <label key={j.id} className="flex cursor-pointer items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={approveJuris.includes(j.id)}
                    onChange={(e) =>
                      setApproveJuris(e.target.checked ? [...approveJuris, j.id] : approveJuris.filter((x) => x !== j.id))
                    }
                  />
                  <span className="font-medium">{j.code}</span>
                  <span className="text-xs text-slate-500">{j.district}, {j.state}{j.department ? ` · ${j.department}` : ""}</span>
                </label>
              ))}
              {juris.length === 0 && <p className="text-xs text-slate-500">No jurisdictions yet - create one first.</p>}
            </div>
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setApproveUser(null)}>{t("common.cancel")}</Button>
            <Button type="submit" loading={busy}>{t("admin.approve")}</Button>
          </div>
        </form>
      </Dialog>

      {/* Create jurisdiction dialog */}
      <Dialog open={jurisOpen} onClose={() => setJurisOpen(false)} title={t("admin.createJurisdiction")}>
        <form onSubmit={createJurisdiction} className="space-y-4">
          <Field label={t("admin.jurisdictionCode")} required hint="e.g. L1_BELAGAVI">
            <Input required value={newJuris.code} onChange={(e) => setNewJuris({ ...newJuris, code: e.target.value })} />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={t("common.district")}>
              <Input value={newJuris.district} onChange={(e) => setNewJuris({ ...newJuris, district: e.target.value })} />
            </Field>
            <Field label={t("common.state")}>
              <Input value={newJuris.state} onChange={(e) => setNewJuris({ ...newJuris, state: e.target.value })} />
            </Field>
            <Field label={t("admin.department")}>
              <Input value={newJuris.department} onChange={(e) => setNewJuris({ ...newJuris, department: e.target.value })} />
            </Field>
            <Field label={t("admin.organization")}>
              <Input value={newJuris.organization} onChange={(e) => setNewJuris({ ...newJuris, organization: e.target.value })} />
            </Field>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setJurisOpen(false)}>{t("common.cancel")}</Button>
            <Button type="submit" loading={busy}>{t("common.save")}</Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
