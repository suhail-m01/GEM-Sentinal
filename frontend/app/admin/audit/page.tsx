"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { ShieldCheck, ShieldX } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Select, Input } from "@/components/ui/fields";
import { Table, TBody, TH, TD, THead, TR } from "@/components/ui/table";
import { Loading } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

interface VerifyReport {
  valid: boolean;
  total_records: number;
  last_hash?: string;
  broken_at_index?: number;
  broken_record_id?: string;
  detail?: string;
}

interface LogRow {
  id: string;
  actor_id: string | null;
  actor_role: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  tender_id: string | null;
  application_id: string | null;
  source_ip: string | null;
  ip_source: string | null;
  request_id: string | null;
  session_id: string | null;
  user_agent: string | null;
  payload: Record<string, unknown> | null;
  prev_hash: string;
  record_hash: string;
  created_at: string;
}

const COMMON_ACTIONS = [
  "override_created",
  "admin_cosign_completed",
  "override_rejected",
  "override_escalated",
  "override_reviewed",
  "vigilance_referral_reviewed",
  "vigilance_referral_ready",
  "vigilance_referral_filed_manually",
  "officer_decision_recommend_qualify",
  "officer_decision_recommend_reject",
  "application_submitted",
  "report_download",
  "blacklist_entry_added",
  "blacklist_status_changed",
  "tender_rules_saved",
  "audit_ip_revealed",
  "audit_ip_reveal_rejected",
  "corrigendum_published",
];

export default function AdminAuditPage() {
  const t = useTranslations();
  const [logs, setLogs] = useState<LogRow[] | null>(null);
  const [action, setAction] = useState("");
  const [verify, setVerify] = useState<VerifyReport | null>(null);
  const [verifying, setVerifying] = useState(false);
  // IP reveal (VIEW_FULL_IP): per-event re-authentication, itself audited.
  const [revealId, setRevealId] = useState<string | null>(null);
  const [revealPw, setRevealPw] = useState("");
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  const [revealBusy, setRevealBusy] = useState(false);
  const [revealErr, setRevealErr] = useState("");

  const load = useCallback(() => {
    const qs = new URLSearchParams({ limit: "200" });
    if (action) qs.set("action", action);
    api<{ logs: LogRow[] }>(`/admin/audit-logs?${qs.toString()}`)
      .then((d) => setLogs(d.logs))
      .catch(() => setLogs([]));
  }, [action]);

  useEffect(() => {
    load();
  }, [load]);

  const runVerify = async () => {
    setVerifying(true);
    try {
      const r = await api<VerifyReport>(`/admin/audit-logs/verify`);
      setVerify(r);
    } finally {
      setVerifying(false);
    }
  };

  const doReveal = async (event_id: string) => {
    setRevealBusy(true);
    setRevealErr("");
    try {
      const r = await api<{ source_ip: string }>(`/admin/audit-logs/reveal-ip`, {
        method: "POST",
        body: JSON.stringify({ event_id, password: revealPw }),
      });
      setRevealed((p) => ({ ...p, [event_id]: r.source_ip }));
      setRevealId(null);
      setRevealPw("");
    } catch (e: any) {
      setRevealErr(e?.detail ?? String(e));
    } finally {
      setRevealBusy(false);
    }
  };

  if (!logs) return <Loading />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">{t("admin.auditTitle")}</h1>
          <p className="text-sm text-slate-500">Every state-changing action is recorded immutably; each record hashes the previous one.</p>
        </div>
        <Button variant="secondary" onClick={runVerify} loading={verifying}>
          {verify?.valid ? <ShieldCheck size={15} /> : <ShieldX size={15} />} {t("admin.verifyChain")}
        </Button>
      </div>

      {verify && (
        <Alert variant={verify.valid ? "success" : "critical"} title={verify.valid ? t("admin.chainValid") : t("admin.chainBroken")}>
          {verify.total_records} records checked
          {verify.broken_at_index !== undefined ? ` - first break at record #${verify.broken_at_index} (${verify.broken_record_id})` : ""}
          {verify.detail ? ` - ${verify.detail}` : ""}
        </Alert>
      )}

      <Card className="p-4">
        <div className="flex flex-wrap gap-3">
          <Select className="max-w-xs" value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">Action: All</option>
            {COMMON_ACTIONS.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </Select>
          <Input className="max-w-xs" placeholder="Custom action…" value={action} onChange={(e) => setAction(e.target.value)} />
        </div>
      </Card>

      <Table>
        <THead>
          <TR>
            <TH>At</TH>
            <TH>Action</TH>
            <TH>Actor</TH>
            <TH>Entity</TH>
            <TH>IP (masked)</TH>
            <TH>Req ID</TH>
            <TH>Record hash</TH>
            <TH>Prev hash</TH>
            <TH>Payload</TH>
          </TR>
        </THead>
        <TBody>
          {logs.map((l) => {
            const fullIp = revealed[l.id];
            return (
              <TR key={l.id}>
                <TD className="whitespace-nowrap text-xs">{fmtDateTime(l.created_at)}</TD>
                <TD className="text-xs font-medium text-slate-800">{l.action}</TD>
                <TD className="text-xs">{l.actor_role ?? "-"}</TD>
                <TD className="text-xs">
                  {l.entity_type}
                  {l.entity_id ? <span className="text-slate-400"> #{l.entity_id.slice(0, 8)}</span> : ""}
                </TD>
                <TD className="whitespace-nowrap font-mono text-[11px]">
                  {l.source_ip ? (
                    <span>
                      <span className={fullIp ? "text-slate-900" : "text-slate-500"}>{fullIp ?? l.source_ip}</span>
                      {l.ip_source && (
                        <span className="ml-1 rounded bg-slate-100 px-1 py-0.5 text-[9px] uppercase text-slate-500">
                          {l.ip_source}
                        </span>
                      )}
                      {!fullIp && (
                        <button
                          className="ml-1.5 text-[10px] font-medium text-sky-600 hover:underline"
                          onClick={() => { setRevealId(l.id); setRevealPw(""); setRevealErr(""); }}
                        >
                          Reveal
                        </button>
                      )}
                    </span>
                  ) : (
                    <span className="text-slate-300">-</span>
                  )}
                </TD>
                <TD className="whitespace-nowrap font-mono text-[10px] text-slate-400">
                  {l.request_id ? l.request_id.slice(0, 10) : "-"}
                </TD>
                <TD className="font-mono text-[10px] text-slate-500">{l.record_hash?.slice(0, 16)}…</TD>
                <TD className="font-mono text-[10px] text-slate-500">{l.prev_hash?.slice(0, 16)}…</TD>
                <TD className="max-w-[280px] truncate font-mono text-[10px] text-slate-400" title={JSON.stringify(l.payload)}>
                  {l.payload ? JSON.stringify(l.payload) : ""}
                </TD>
              </TR>
            );
          })}
        </TBody>
      </Table>

      <Dialog open={revealId !== null} onClose={() => setRevealId(null)} title="Reveal full source IP">
        <div className="space-y-3">
          <p className="text-sm text-slate-600">
            Viewing the full source IP requires the <span className="font-mono">VIEW_FULL_IP</span> permission and
            password re-authentication. This action is itself recorded as a high-severity audit event.
          </p>
          <div className="text-xs text-slate-500">
            Event <span className="font-mono">{revealId?.slice(0, 8)}…</span>
          </div>
          <Input
            type="password"
            placeholder="Your password"
            value={revealPw}
            onChange={(e) => setRevealPw(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && revealPw) doReveal(revealId!); }}
          />
          {revealErr && <Alert variant="danger">{revealErr}</Alert>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRevealId(null)}>Cancel</Button>
            <Button variant="secondary" onClick={() => doReveal(revealId!)} loading={revealBusy} disabled={!revealPw}>
              Reveal (audited)
            </Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
