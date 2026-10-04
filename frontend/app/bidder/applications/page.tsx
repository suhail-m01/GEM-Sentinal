"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TBody, TH, TD, THead, TR } from "@/components/ui/table";
import { Loading, Empty, AppStatusBadge, RiskBadge } from "@/components/app/widgets";
import { fmtDate } from "@/lib/utils";

interface Row {
  id: string;
  application_no: string;
  tender_no: string;
  tender_title: string;
  status: string;
  eligibility_result: string | null;
  compliance_score: number | null;
  risk_level: string | null;
  submitted_at: string | null;
}

export default function ApplicationsListPage() {
  const t = useTranslations();
  const [rows, setRows] = useState<Row[] | null>(null);

  useEffect(() => {
    api<{ applications: Row[] }>(`/bidder/applications`).then((d) => setRows(d.applications)).catch(() => setRows([]));
  }, []);

  if (!rows) return <Loading />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("nav.applications")}</h1>
        <p className="text-sm text-slate-500">{t("bidder.trackApplication")} - status, risk & verification summary</p>
      </div>

      {rows.length === 0 ? (
        <Empty text={t("common.noData")} />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Application</TH>
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
                <TD className="font-medium text-navy-900">{r.application_no}</TD>
                <TD>
                  <div className="text-sm">{r.tender_no}</div>
                  <div className="text-xs text-slate-500">{r.tender_title}</div>
                </TD>
                <TD><AppStatusBadge status={r.status} /></TD>
                <TD><RiskBadge risk={r.risk_level} /></TD>
                <TD>{r.compliance_score ?? "-"}</TD>
                <TD className="text-xs">{r.submitted_at ? fmtDate(r.submitted_at) : "-"}</TD>
                <TD>
                  <Link href={`/bidder/applications/${r.id}`} className="text-sm font-medium text-navy-700 hover:underline">
                    {t("common.view")} →
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
