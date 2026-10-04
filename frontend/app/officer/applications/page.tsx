"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Flag, Lock, Search } from "lucide-react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/fields";
import { Table, TBody, TH, TD, THead, TR } from "@/components/ui/table";
import { Loading, Empty, AppStatusBadge, RiskBadge } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

interface Row {
  id: string;
  application_no: string;
  company: string | null;
  tender_no: string;
  tender_title?: string;
  district?: string;
  status: string;
  risk_level: string | null;
  compliance_score: number | null;
  eligibility_result?: string | null;
  fraud_flagged?: boolean;
  report_stale?: boolean;
  sla_breached?: boolean;
  submitted_at?: string | null;
  sealed?: boolean;
  bid_deadline_utc?: string | null;
}

const STATUSES = [
  "Sealed",
  "AI Verification Not Run",
  "AI Verification In Progress",
  "Pending L1 Review",
  "Clarification Requested",
  "Resubmitted",
  "L1 Recommended",
  "Pending L2 Approval",
  "Admin Co-sign Required",
  "Approved",
  "Rejected",
  "Withdrawn",
];

export default function OfficerQueuePage() {
  const t = useTranslations();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [status, setStatus] = useState("");
  const [risk, setRisk] = useState("");
  const [q, setQ] = useState("");

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (risk) params.set("risk", risk);
    if (q) params.set("q", q);
    api<{ applications: Row[] }>(`/officer/applications?${params.toString()}`).then((d) => setRows(d.applications));
  }, [status, risk, q]);

  useEffect(() => {
    load();
  }, [load]);

  if (!rows) return <Loading />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("nav.queue")}</h1>
        <p className="text-sm text-slate-500">Applications routed to your jurisdiction - {rows.length} total</p>
      </div>

      <Card className="p-4">
        <div className="grid gap-3 md:grid-cols-4">
          <div className="relative">
            <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
            <Input className="pl-9" placeholder="App no / tender…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <Select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">{t("common.status")}: All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </Select>
          <Select value={risk} onChange={(e) => setRisk(e.target.value)}>
            <option value="">{t("common.risk")}: All</option>
            {["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </Select>
          <button
            className="justify-self-end rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"
            onClick={() => {
              setStatus("");
              setRisk("");
              setQ("");
            }}
          >
            {t("common.reset")}
          </button>
        </div>
      </Card>

      {rows.length === 0 ? (
        <Empty text={t("common.noData")} />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Application</TH>
              <TH>{t("common.bidder")}</TH>
              <TH>{t("common.tender")}</TH>
              <TH>{t("common.status")}</TH>
              <TH>{t("common.risk")}</TH>
              <TH>{t("common.score")}</TH>
              <TH>{t("common.date")}</TH>
              <TH>{t("common.actions")}</TH>
            </TR>
          </THead>
          <TBody>
            {rows.map((r) => (
              <TR key={r.id}>
                <TD className="font-medium text-navy-900">
                  {r.application_no}
                  {r.sealed && (
                    <span className="ml-1 inline-flex items-center gap-1 rounded bg-navy-50 px-1.5 py-0.5 text-[11px] font-semibold text-navy-700">
                      <Lock size={10} /> {t("officer.sealedBadge")}
                    </span>
                  )}
                  {r.sla_breached && <span title="SLA breached" className="ml-1 text-red-600">⏱</span>}
                  {r.report_stale && <span title="Report stale" className="ml-1 text-amber-600">⟳</span>}
                  {r.fraud_flagged && <Flag size={12} className="ml-1 inline text-red-600" aria-label="Fraud flagged" />}
                </TD>
                <TD className="text-slate-600">{r.sealed ? t("officer.sealedConfidential") : r.company ?? "-"}</TD>
                <TD>
                  <div className="text-sm">{r.tender_no}</div>
                  <div className="text-xs text-slate-500">{r.district ?? ""}</div>
                </TD>
                <TD>
                  {r.sealed ? (
                    <span className="inline-flex items-center gap-1 rounded-md bg-navy-50 px-2 py-1 text-xs font-medium text-navy-700">
                      <Lock size={11} /> {t("officer.sealedBadge")}
                    </span>
                  ) : (
                    <AppStatusBadge status={r.status} />
                  )}
                </TD>
                <TD>{r.sealed ? "-" : <RiskBadge risk={r.risk_level} />}</TD>
                <TD>{r.sealed ? "-" : r.compliance_score ?? "-"}</TD>
                <TD className="text-xs">
                  {r.sealed && r.bid_deadline_utc ? (
                    <span title={t("officer.sealedOpensAt")}>{fmtDateTime(r.bid_deadline_utc)}</span>
                  ) : r.submitted_at ? fmtDateTime(r.submitted_at) : "-"}
                </TD>
                <TD>
                  <Link href={`/officer/review/${r.id}`} className="text-sm font-medium text-navy-700 hover:underline">
                    {t("officer.review")} →
                  </Link>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </div>
  );
}
