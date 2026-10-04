"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Activity, Download, Gauge, RefreshCw, Siren, Waves } from "lucide-react";
import { api, downloadFile, post } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Card } from "@/components/ui/card";
import { Select } from "@/components/ui/fields";
import { Table, TBody, TH, TD, THead, TR } from "@/components/ui/table";
import { Loading, Empty, RiskBadge } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

interface Watch {
  id: string;
  application_id: string;
  application_no: string;
  bidder_name: string | null;
  tender_no: string | null;
  active: boolean;
  watched_attributes: string[] | null;
  score_now: number | null;
  last_polled_at: string | null;
  created_at: string | null;
}
interface Drift {
  id: string;
  application_id: string;
  application_no: string | null;
  bidder_name: string | null;
  attribute: string;
  old_value: Record<string, unknown> | null;
  new_value: Record<string, unknown> | null;
  direction: string;
  severity: string;
  detected_at: string | null;
  score_before: number | null;
  score_after: number | null;
  cause_summary: string | null;
  alert_status: string | null;
}
interface Alert {
  id: string;
  application_no: string | null;
  attribute: string;
  severity: string;
  title: string;
  message: string;
  status: string;
  resolution_reason: string | null;
  created_at: string | null;
}
interface Comparison {
  application_no: string;
  bidder_name: string | null;
  score_then: number | null;
  score_now: number | null;
  risk_now: string | null;
  active_watch: boolean;
  changed_attributes: { attribute: string; from: string | null; to: string | null; severity: string }[];
}
interface SourceHealth {
  sources: Record<string, { last_poll: string | null; last_poll_at: string | null; source_status: string | null }>;
}

const SEV_STYLE: Record<string, string> = {
  CRITICAL: "bg-red-100 text-red-700 border-red-300",
  HIGH: "bg-orange-100 text-orange-700 border-orange-300",
  MEDIUM: "bg-amber-100 text-amber-700 border-amber-300",
  LOW: "bg-slate-100 text-slate-600 border-slate-300",
};
const ALERT_STYLE: Record<string, string> = {
  OPEN: "bg-red-50 text-red-700 border-red-200",
  SNOOZED: "bg-amber-50 text-amber-700 border-amber-200",
  ESCALATED: "bg-purple-50 text-purple-700 border-purple-200",
  ACKNOWLEDGED: "bg-emerald-50 text-emerald-700 border-emerald-200",
};

function SevBadge({ s }: { s: string }) {
  return (
    <span className={`inline-flex rounded-md border px-2 py-0.5 text-[11px] font-bold ${SEV_STYLE[s] ?? SEV_STYLE.LOW}`}>
      {s}
    </span>
  );
}
function AlertBadge({ s }: { s: string | null }) {
  if (!s) return <span className="text-xs text-slate-400">-</span>;
  return (
    <span className={`inline-flex rounded-md border px-2 py-0.5 text-[11px] font-semibold ${ALERT_STYLE[s] ?? ALERT_STYLE.OPEN}`}>
      {s}
    </span>
  );
}
function attrVal(v: Record<string, unknown> | null): string {
  if (!v) return "-";
  return String(v.inner_status || v.source_status || "-");
}

export default function WatchTowerPage() {
  const t = useTranslations();
  const { user } = useAuth();
  const canAct = user?.role === "PROCUREMENT_OFFICER" || user?.role === "ADMIN";

  const [watches, setWatches] = useState<Watch[] | null>(null);
  const [drifts, setDrifts] = useState<Drift[] | null>(null);
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [health, setHealth] = useState<SourceHealth | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [selectedApp, setSelectedApp] = useState<string>("");
  const [sevFilter, setSevFilter] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [tickInfo, setTickInfo] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ watches: Watch[] }>("/watch/watches").then((d) => {
      setWatches(d.watches.filter((w) => w.active));
      if (!selectedApp && d.watches.length) setSelectedApp(d.watches[0].application_id);
    }).catch(() => setWatches([]));
    api<{ drifts: Drift[] }>(`/watch/drift${sevFilter ? `?severity=${sevFilter}` : ""}`).then((d) => setDrifts(d.drifts)).catch(() => setDrifts([]));
    api<{ alerts: Alert[] }>("/watch/alerts").then((d) => setAlerts(d.alerts)).catch(() => setAlerts([]));
    api<SourceHealth>("/watch/source-health").then(setHealth).catch(() => setHealth(null));
  }, [sevFilter, selectedApp]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!selectedApp) { setComparison(null); return; }
    api<Comparison>(`/watch/applications/${selectedApp}/comparison`).then(setComparison).catch(() => setComparison(null));
  }, [selectedApp]);

  const run = useCallback(async (kind: string, fn: () => Promise<unknown>, msg?: string) => {
    setBusy(kind);
    setNote(null);
    try {
      const r = (await fn()) as {
        tick?: { drifts_detected?: number; watches_polled?: number };
        drifts_detected?: number; watches_polled?: number;
      } | undefined;
      const tick = r?.tick ?? (r && "drifts_detected" in r ? { drifts_detected: r.drifts_detected, watches_polled: r.watches_polled } : undefined);
      setNote(msg ?? (tick ? `${t("watch.tickRan")} - ${tick.drifts_detected ?? 0} ${t("watch.driftsFound")}` : t("watch.done")));
      if (tick) setTickInfo(`${new Date().toLocaleTimeString()} · ${tick.watches_polled ?? 0} ${t("watch.watchesPolled")} · ${tick.drifts_detected ?? 0} ${t("watch.driftsFound")}`);
      load();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, [load, t]);

  const openDrifts = useMemo(() => (drifts ?? []).length, [drifts]);
  const openAlerts = useMemo(() => (alerts ?? []).filter((a) => a.status === "OPEN").length, [alerts]);
  const criticalOpen = useMemo(() => (alerts ?? []).filter((a) => a.status === "OPEN" && a.severity === "CRITICAL").length, [alerts]);

  if (!watches || !drifts || !alerts) return <Loading />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold text-navy-900">
            <Waves size={20} className="text-navy-600" /> {t("watch.title")}
          </h1>
          <p className="text-sm text-slate-500">{t("watch.subtitle")}</p>
        </div>
        {canAct && (
          <button
            disabled={busy !== null}
            onClick={() => run("run-now", () => post("/watch/run-now"))}
            className="inline-flex items-center gap-2 rounded-md bg-navy-800 px-3 py-2 text-sm font-medium text-white hover:bg-navy-700 disabled:opacity-50"
          >
            <RefreshCw size={14} className={busy === "run-now" ? "animate-spin" : ""} /> {t("watch.runNow")}
          </button>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        <Card className="p-4">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500"><Activity size={13} /> {t("watch.activeWatches")}</div>
          <div className="mt-1 text-2xl font-bold text-navy-900">{watches.length}</div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500"><Siren size={13} /> {t("watch.openAlerts")}</div>
          <div className="mt-1 text-2xl font-bold text-navy-900">{openAlerts}</div>
          {criticalOpen > 0 && <div className="text-xs font-semibold text-red-600">{criticalOpen} {t("watch.criticalOpen")}</div>}
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-500"><Gauge size={13} /> {t("watch.driftEvents")}</div>
          <div className="mt-1 text-2xl font-bold text-navy-900">{openDrifts}</div>
        </Card>
        <Card className="p-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("watch.lastTick")}</div>
          <div className="mt-1 text-xs text-slate-600">{tickInfo ?? t("watch.autoTick")}</div>
        </Card>
      </div>

      {note && <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">{note}</div>}

      {/* Active watches + Then vs Now */}
      <Card className="p-4">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-sm font-bold text-navy-900">{t("watch.activeWatches")}</h2>
          {canAct && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-slate-500">{t("watch.demoHint")}:</span>
              <select
                className="rounded border border-slate-300 px-2 py-1 text-xs"
                value={selectedApp}
                onChange={(e) => setSelectedApp(e.target.value)}
              >
                {watches.map((w) => (
                  <option key={w.id} value={w.application_id}>{w.application_no} - {w.bidder_name}</option>
                ))}
              </select>
              <button
                disabled={busy !== null || !selectedApp}
                onClick={() => run("simulate", () => post("/watch/demo/simulate-drift", { application_id: selectedApp, to_status: "CANCELLED" }), t("watch.simulated"))}
                className="rounded border border-red-300 bg-red-50 px-2 py-1 font-medium text-red-700 hover:bg-red-100 disabled:opacity-50"
              >
                {t("watch.simulateDrift")}
              </button>
              <button
                disabled={busy !== null || !selectedApp}
                onClick={() => run("restore", () => post("/watch/demo/restore-drift", { application_id: selectedApp }), t("watch.restored"))}
                className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
              >
                {t("watch.restore")}
              </button>
            </div>
          )}
        </div>
        {watches.length === 0 ? (
          <Empty text={t("watch.noWatches")} />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>{t("common.application")}</TH>
                <TH>{t("common.bidder")}</TH>
                <TH>{t("watch.scoreThen")} / {t("watch.scoreNow")}</TH>
                <TH>{t("watch.watchedAttributes")}</TH>
                <TH>{t("watch.lastPolled")}</TH>
                {canAct && <TH>{t("common.actions")}</TH>}
              </TR>
            </THead>
            <TBody>
              {watches.map((w) => (
                <TR key={w.id} className={w.application_id === selectedApp ? "bg-navy-50/60" : ""}>
                  <TD className="font-medium text-navy-900">
                    {w.application_no}
                    <div className="text-xs text-slate-500">{w.tender_no}</div>
                  </TD>
                  <TD className="text-slate-600">{w.bidder_name ?? "-"}</TD>
                  <TD>
                    {comparison?.application_no === w.application_no && comparison.score_then !== null ? (
                      <span className={comparison.score_now !== null && (comparison.score_now ?? 0) < (comparison.score_then ?? 0) ? "font-bold text-red-600" : "font-bold text-navy-900"}>
                        {comparison.score_then} → {comparison.score_now ?? "-"}
                      </span>
                    ) : w.score_now !== null ? w.score_now : "-"}
                  </TD>
                  <TD>
                    <div className="flex max-w-[260px] flex-wrap gap-1">
                      {(w.watched_attributes ?? []).map((a) => (
                        <span key={a} className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold text-slate-600">{a}</span>
                      ))}
                    </div>
                  </TD>
                  <TD className="text-xs text-slate-500">{w.last_polled_at ? fmtDateTime(w.last_polled_at) : "-"}</TD>
                  {canAct && (
                    <TD className="text-xs">
                      <div className="flex gap-3">
                        <button
                          disabled={busy !== null}
                          onClick={() => run("gate", () => post(`/watch/applications/${w.application_id}/gate-check`), t("watch.gateChecked"))}
                          className="font-medium text-navy-700 hover:underline disabled:opacity-50"
                        >
                          {t("watch.gateCheck")}
                        </button>
                        <a
                          href="#"
                          onClick={(e) => { e.preventDefault(); setSelectedApp(w.application_id); }}
                          className="font-medium text-navy-700 hover:underline"
                        >
                          {t("watch.thenVsNow")}
                        </a>
                      </div>
                    </TD>
                  )}
                </TR>
              ))}
            </TBody>
          </Table>
        )}

        {comparison && (
          <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <span className="font-bold text-navy-900">{comparison.application_no} · {comparison.bidder_name}</span>
              <span>
                {t("watch.scoreThen")}: <b>{comparison.score_then ?? "-"}</b> → {t("watch.scoreNow")}:{" "}
                <b className={(comparison.score_now ?? 0) < (comparison.score_then ?? 0) ? "text-red-600" : "text-emerald-700"}>
                  {comparison.score_now ?? "-"}
                </b>
              </span>
              {comparison.risk_now && <span>{t("common.risk")}: <RiskBadge risk={comparison.risk_now} /></span>}
            </div>
            {comparison.changed_attributes.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {comparison.changed_attributes.map((c, i) => (
                  <span key={i} className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium ${SEV_STYLE[c.severity] ?? SEV_STYLE.LOW}`}>
                    {c.attribute}: {c.from || "-"} → {c.to || "-"}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </Card>

      {/* Drift feed */}
      <Card className="p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-bold text-navy-900">{t("watch.driftFeed")}</h2>
          <Select className="w-36" value={sevFilter} onChange={(e) => setSevFilter(e.target.value)}>
            <option value="">{t("common.risk")}: {t("common.all")}</option>
            {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
        </div>
        {drifts.length === 0 ? (
          <Empty text={t("watch.noDrifts")} />
        ) : (
          <Table>
            <THead>
              <TR>
                <TH>{t("watch.attribute")}</TH>
                <TH>{t("common.application")}</TH>
                <TH>{t("watch.thenNow")}</TH>
                <TH>{t("watch.direction")}</TH>
                <TH>{t("common.risk")}</TH>
                <TH>{t("watch.scoreDelta")}</TH>
                <TH>{t("common.date")}</TH>
                <TH>{t("watch.alertState")}</TH>
                <TH>{t("common.actions")}</TH>
              </TR>
            </THead>
            <TBody>
              {drifts.map((d) => (
                <TR key={d.id}>
                  <TD className="font-semibold text-navy-900">{d.attribute}</TD>
                  <TD>
                    <div className="text-sm">{d.application_no}</div>
                    <div className="text-xs text-slate-500">{d.bidder_name}</div>
                  </TD>
                  <TD className="text-sm">{attrVal(d.old_value)} → <b>{attrVal(d.new_value)}</b></TD>
                  <TD>
                    <span className={`text-xs font-semibold ${d.direction === "DEGRADED" ? "text-red-600" : d.direction === "IMPROVED" ? "text-emerald-600" : "text-slate-500"}`}>
                      {d.direction}
                    </span>
                  </TD>
                  <TD><SevBadge s={d.severity} /></TD>
                  <TD className="text-sm">{d.score_before ?? "-"} → {d.score_after ?? "-"}</TD>
                  <TD className="text-xs text-slate-500">{d.detected_at ? fmtDateTime(d.detected_at) : "-"}</TD>
                  <TD><AlertBadge s={d.alert_status} /></TD>
                  <TD>
                    <button
                      onClick={() => downloadFile(`/watch/drift/${d.id}/report.pdf`, `compliance-drift-${d.id.slice(0, 8)}.pdf`).catch(() => undefined)}
                      className="inline-flex items-center gap-1 text-sm font-medium text-navy-700 hover:underline"
                      title={t("watch.driftReportPdf")}
                    >
                      <Download size={13} /> PDF
                    </button>
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </Card>

      {/* Alerts */}
      <Card className="p-4">
        <h2 className="mb-3 text-sm font-bold text-navy-900">{t("watch.alertFeed")}</h2>
        {alerts.length === 0 ? (
          <Empty text={t("watch.noAlerts")} />
        ) : (
          <div className="space-y-2">
            {alerts.slice(0, 20).map((a) => (
              <div key={a.id} className="rounded-md border border-slate-200 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <SevBadge s={a.severity} />
                  <AlertBadge s={a.status} />
                  <span className="text-sm font-semibold text-navy-900">{a.title}</span>
                  <span className="ml-auto text-xs text-slate-400">{a.created_at ? fmtDateTime(a.created_at) : ""}</span>
                </div>
                <p className="mt-1 text-xs text-slate-600">{a.message}</p>
                <div className="mt-1 text-[11px] text-slate-400">
                  {t("watch.routedTo")}: {a.resolution_reason ?? "-"}
                </div>
                {canAct && a.status !== "ACKNOWLEDGED" && (
                  <div className="mt-2 flex gap-2">
                    <button
                      disabled={busy !== null}
                      onClick={() => run(`ack-${a.id}`, () => post(`/watch/alerts/${a.id}/acknowledge`), t("watch.acknowledged"))}
                      className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50"
                    >
                      {t("watch.acknowledge")}
                    </button>
                    <button
                      disabled={busy !== null || a.severity === "CRITICAL"}
                      title={a.severity === "CRITICAL" ? t("watch.noSnoozeCritical") : undefined}
                      onClick={() => {
                        const reason = window.prompt(t("watch.snoozeReason"), "");
                        if (!reason || reason.trim().length < 5) return;
                        run(`snooze-${a.id}`, () => post(`/watch/alerts/${a.id}/snooze`, { reason: reason.trim(), hours: 12 }), t("watch.snoozed"));
                      }}
                      className="rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-medium text-amber-700 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {t("watch.snooze")}
                    </button>
                    <button
                      disabled={busy !== null}
                      onClick={() => run(`esc-${a.id}`, () => post(`/watch/alerts/${a.id}/escalate`, { note: null }), t("watch.escalated"))}
                      className="rounded border border-purple-300 bg-purple-50 px-2 py-1 text-xs font-medium text-purple-700 hover:bg-purple-100 disabled:opacity-50"
                    >
                      {t("watch.escalate")}
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Source health (watch poll perspective) */}
      <Card className="p-4">
        <h2 className="mb-3 text-sm font-bold text-navy-900">{t("watch.sourceHealth")}</h2>
        {!health || Object.keys(health.sources).length === 0 ? (
          <Empty text={t("watch.noPollsYet")} />
        ) : (
          <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {Object.entries(health.sources).map(([s, h]) => (
              <div key={s} className="rounded-md border border-slate-200 p-3 text-center">
                <div className="text-xs font-bold text-navy-900">{s}</div>
                <div className={`mt-1 text-xs font-semibold ${h.last_poll === "UP" ? "text-emerald-600" : "text-red-600"}`}>
                  {h.last_poll ?? "-"}
                </div>
                <div className="mt-0.5 text-[10px] text-slate-400">{h.last_poll_at ? fmtDateTime(h.last_poll_at) : ""}</div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
