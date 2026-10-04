"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  ArrowDownRight, ArrowUpRight, CheckCircle2, Clock, Download, Play,
  ShieldCheck, Square, PauseCircle, RefreshCw,
} from "lucide-react";
import { api, downloadFile } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { AuctionIntegrity, AuctionSnapshot } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Field, Input, Textarea } from "@/components/ui/fields";
import { Loading } from "@/components/app/widgets";
import { fmtDateTime, formatINR } from "@/lib/utils";

const STATUS_CLS: Record<string, string> = {
  DRAFT: "bg-slate-100 text-slate-700",
  SCHEDULED: "bg-sky-100 text-sky-800",
  LIVE: "bg-emerald-100 text-emerald-800",
  EXTENDED: "bg-amber-100 text-amber-800",
  PAUSED: "bg-amber-100 text-amber-800",
  RESULT_PENDING: "bg-indigo-100 text-indigo-800",
  COMPLETED: "bg-emerald-100 text-emerald-800",
  CANCELLED: "bg-red-100 text-red-700",
};

/** Inline reauth + optional reason panel for protected auction operations. */
function ProtectedAction({
  label, icon, danger, needReason, needRemarks, placeholderReason, onSubmit,
}: {
  label: string;
  icon?: React.ReactNode;
  danger?: boolean;
  needReason?: boolean;
  needRemarks?: boolean;
  placeholderReason?: string;
  onSubmit: (reauthToken: string, extra: { reason?: string; remarks?: string }) => Promise<string | null>;
}) {
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [reason, setReason] = useState("");
  const [remarks, setRemarks] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const run = async () => {
    setErr(null); setOk(null);
    if (!pw) { setErr("Password required."); return; }
    if (needReason && reason.trim().length < 10) { setErr("Provide a reason (at least 10 characters) - it becomes part of the audited record."); return; }
    setBusy(true);
    try {
      const ra = await api<{ reauth_token: string }>("/auth/reauth", {
        method: "POST", body: JSON.stringify({ password: pw }),
      });
      const e = await onSubmit(ra.reauth_token, { reason: reason || undefined, remarks: remarks || undefined });
      if (e) setErr(e);
      else { setOk(`${label} - done.`); setPw(""); setReason(""); setRemarks(""); }
    } catch (e: unknown) {
      const d = (e as { detail?: { message?: string } })?.detail;
      setErr(d?.message || String((e as Error)?.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-start gap-1.5">
      <Button variant={danger ? "destructive" : "default"} onClick={() => setOpen((v) => !v)} disabled={busy}>
        {icon} {label}
      </Button>
      {open && (
        <div className="w-full rounded-lg border border-slate-200 bg-slate-50 p-3">
          <Field label="Confirm password (re-authentication)" required>
            <Input type="password" value={pw} onChange={(e) => setPw(e.target.value)} className="w-full" />
          </Field>
          {needReason && (
            <div className="mt-2">
              <Field label="Reason (audited)" required>
                <Textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder={placeholderReason || "Why is this action being taken?"} className="w-full" />
              </Field>
            </div>
          )}
          {needRemarks && (
            <div className="mt-2">
              <Field label="Remarks (audited)">
                <Textarea value={remarks} onChange={(e) => setRemarks(e.target.value)} className="w-full" />
              </Field>
            </div>
          )}
          <div className="mt-2 flex items-center gap-3">
            <Button size="sm" onClick={run} disabled={busy}>{busy ? "Working…" : "Execute"}</Button>
            {err && <span className="text-xs text-red-700">{err}</span>}
            {ok && <span className="text-xs text-emerald-700">{ok}</span>}
          </div>
        </div>
      )}
    </div>
  );
}

export default function OfficerAuctionMonitorPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const [snap, setSnap] = useState<AuctionSnapshot | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [integrity, setIntegrity] = useState<AuctionIntegrity | null>(null);
  const [schedStart, setSchedStart] = useState("");
  const [schedEnd, setSchedEnd] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const isCfa = user?.role === "CFA" || user?.role === "ADMIN";

  const load = useCallback(() => {
    api<AuctionSnapshot>(`/officer/auctions/${id}`)
      .then((d) => { setSnap(d); setErr(null); })
      .catch((e) => setErr(String(e.detail?.message || e.message || e)));
  }, [id]);
  useEffect(() => { load(); const iv = setInterval(load, 3000); return () => clearInterval(iv); }, [load]);

  const loadIntegrity = () => {
    api<AuctionIntegrity>(`/officer/auctions/${id}/integrity`).then(setIntegrity).catch((e) => setErr(String(e.detail?.message || e.message || e)));
  };

  const act = async (path: string, body?: unknown, method = "POST") => {
    setMsg(null);
    try {
      const r = await api<{ status?: string }>(path, body === undefined ? {} : { method, body: JSON.stringify(body) });
      setMsg(`OK - status ${r.status || ""}`.trim());
      load();
    } catch (e: unknown) {
      const d = (e as { detail?: { message?: string; code?: string } })?.detail;
      setErr(`${d?.code ? d.code + " - " : ""}${d?.message || "Action failed."}`);
    }
  };

  if (err && !snap) return <Alert variant="critical" title="Could not load the auction">{err}</Alert>;
  if (!snap) return <Loading />;

  const reverse = snap.auction_type !== "FORWARD";
  const live = snap.status === "LIVE" || snap.status === "EXTENDED";
  const money = (v: string | null | undefined) => (v ? formatINR(parseFloat(v)) : "-");

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            {reverse ? <ArrowDownRight size={18} className="text-red-700" /> : <ArrowUpRight size={18} className="text-emerald-700" />}
            <h1 className="text-xl font-bold text-navy-900">{snap.auction_no}</h1>
            <Badge className={STATUS_CLS[snap.status] || "bg-slate-100 text-slate-700"}>{snap.status}</Badge>
          </div>
          <p className="text-sm text-slate-500">{snap.title} · {snap.auction_type === "FORWARD" ? "forward e-auction - bids increase, highest wins" : "reverse auction - bids decrease, lowest wins"}</p>
        </div>
        <div className="flex items-center gap-2">
          {live && snap.seconds_remaining != null && (
            <div className="flex items-center gap-2 rounded-lg bg-navy-900 px-4 py-2 text-white">
              <Clock size={16} />
              <span className="font-mono text-lg font-semibold tabular-nums">
                {Math.floor(snap.seconds_remaining / 60)}:{(snap.seconds_remaining % 60).toString().padStart(2, "0")}
              </span>
            </div>
          )}
          <Button variant="ghost" onClick={load} className="text-slate-500"><RefreshCw size={14} /> Refresh</Button>
        </div>
      </div>

      {msg && <Alert variant="success" title="Done">{msg}</Alert>}
      {err && snap && <Alert variant="critical" title="Action failed">{err}</Alert>}

      {/* controls */}
      <Card>
        <CardHeader><CardTitle className="text-sm">Auction controls</CardTitle>
          <CardDescription>Every state change is audited on the tamper-evident ledger; protected operations require password re-authentication.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {snap.status === "DRAFT" && (
            <div className="space-y-3">
              <div className="grid gap-3 md:grid-cols-3">
                <Field label="Start (UTC, HH:MM optional)">
                  <Input value={schedStart} onChange={(e) => setSchedStart(e.target.value)} placeholder="YYYY-MM-DDTHH:MM:SS" />
                </Field>
                <Field label="End (UTC)">
                  <Input value={schedEnd} onChange={(e) => setSchedEnd(e.target.value)} placeholder="YYYY-MM-DDTHH:MM:SS" />
                </Field>
                <div className="flex items-end gap-2">
                  <Button onClick={() => act(`/officer/auctions/${id}/schedule`, { scheduled_start_at: schedStart, scheduled_end_at: schedEnd })}>Schedule</Button>
                  <ProtectedAction label="Cancel auction" danger needReason icon={<Square size={14} />}
                    onSubmit={(tok, x) => act(`/officer/auctions/${id}/cancel`, { reason: x.reason, reauth_token: tok }).then(() => null)} />
                </div>
              </div>
              <p className="text-xs text-slate-500">The auction starts only at the server-side scheduled time (server-authoritative clock).</p>
            </div>
          )}
          {snap.status === "SCHEDULED" && (
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm text-slate-600">
                Scheduled {fmtDateTime(snap.scheduled_start_at)} → {fmtDateTime(snap.effective_end_at || snap.scheduled_end_at)} (UTC)
              </p>
              <Button onClick={() => act(`/officer/auctions/${id}/start`, {})}><Play size={14} /> Start now</Button>
              <ProtectedAction label="Cancel auction" danger needReason icon={<Square size={14} />}
                onSubmit={(tok, x) => act(`/officer/auctions/${id}/cancel`, { reason: x.reason, reauth_token: tok }).then(() => null)} />
            </div>
          )}
          {live && (
            <div className="flex flex-wrap items-center gap-3">
              <ProtectedAction label="Pause auction" danger needReason icon={<PauseCircle size={14} />}
                placeholderReason="Reason for pausing (e.g. participant network verification)"
                onSubmit={(tok, x) => act(`/officer/auctions/${id}/pause`, { reason: x.reason, reauth_token: tok }).then(() => null)} />
              <Button onClick={() => act(`/officer/auctions/${id}/close`, {})}><CheckCircle2 size={14} /> Close & compute ranking</Button>
              <p className="text-xs text-slate-500">Close freezes final prices and computes the deterministic ranking (L1 is not auto-awarded - the CFA decides).</p>
            </div>
          )}
          {snap.status === "PAUSED" && (
            <div className="flex flex-wrap items-center gap-3">
              <ProtectedAction label="Resume auction" icon={<Play size={14} />}
                onSubmit={(tok, x) => act(`/officer/auctions/${id}/resume`, { reauth_token: tok, remarks: x.remarks || "Resume authorized." }).then(() => null)} />
              <Button onClick={() => act(`/officer/auctions/${id}/close`, {})}><CheckCircle2 size={14} /> Close & compute ranking</Button>
            </div>
          )}
          {snap.status === "RESULT_PENDING" && (
            <div className="space-y-3">
              <p className="text-sm text-slate-600">Ranking computed. The Competent Authority must approve or reject the result.</p>
              {isCfa ? (
                <div className="flex flex-wrap gap-3">
                  <ProtectedAction label="Approve result" icon={<CheckCircle2 size={14} />} needRemarks
                    placeholderReason=""
                    onSubmit={(tok, x) => act(`/officer/auctions/${id}/approve-result`, { decision: "APPROVE", remarks: x.remarks || "Result approved.", reauth_token: tok }).then(() => null)} />
                  <ProtectedAction label="Reject result" danger icon={<Square size={14} />} needRemarks
                    onSubmit={(tok, x) => act(`/officer/auctions/${id}/approve-result`, { decision: "REJECT", remarks: x.remarks || "Result rejected for review.", reauth_token: tok }).then(() => null)} />
                </div>
              ) : (
                <p className="text-xs text-slate-500">Only the CFA/ADMIN can approve or reject the result.</p>
              )}
            </div>
          )}
          {(snap.status === "COMPLETED" || snap.status === "CANCELLED") && (
            <p className="text-sm text-slate-500">
              Terminal state: {snap.status}{snap.closed_at ? ` (closed ${fmtDateTime(snap.closed_at)})` : ""}.
            </p>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* market state */}
        <Card>
          <CardHeader><CardTitle className="text-sm">Market state</CardTitle></CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="text-xs text-slate-400">Leading {snap.leading && !reverse ? "(highest)" : snap.leading && reverse ? "(lowest)" : ""}</div>
                <div className="text-2xl font-bold text-navy-900">{money(snap.leading?.amount)}</div>
                <div className="font-mono text-xs text-slate-500">{snap.leading ? `${snap.leading.anonymous_id}${snap.leading.sequence_number ? ` · #${snap.leading.sequence_number}` : ""}` : "no revised bids yet"}</div>
              </div>
              <div className="grid grid-cols-2 gap-y-1.5 text-xs">
                <span className="text-slate-400">Min increment</span><span className="font-medium">{money(snap.min_increment)}</span>
                <span className="text-slate-400">Participants</span><span className="font-medium">{snap.participant_count}</span>
                <span className="text-slate-400">Extensions</span><span className="font-medium">{snap.extension_count}</span>
                <span className="text-slate-400">Open ref.</span><span className="font-medium">{money(snap.opening_reference_price)}</span>
              </div>
            </div>
            {/* participants */}
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead><tr className="border-b border-slate-200 text-slate-400">
                  <th className="py-1.5 pr-2">Bidder</th><th className="py-1.5 pr-2">Company</th><th className="py-1.5 pr-2">Qualified</th><th className="py-1.5 text-right">Initial price</th>
                </tr></thead>
                <tbody>
                  {snap.participants.map((p) => (
                    <tr key={p.anonymous_id} className="border-b border-slate-100 last:border-0">
                      <td className="py-1.5 pr-2 font-mono">{p.anonymous_id}</td>
                      <td className="py-1.5 pr-2">{p.company || "-"}</td>
                      <td className="py-1.5 pr-2">{p.ra_eligible ? <Badge className="bg-emerald-100 text-emerald-800">Yes</Badge>
                        : <span title={p.qualification_reason || ""}><Badge className="bg-red-100 text-red-700">No</Badge></span>}</td>
                      <td className="py-1.5 text-right font-medium">{money(p.initial_price)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* result + integrity */}
        <div className="space-y-4">
          {snap.result && (
            <Card>
              <CardHeader><CardTitle className="text-sm">Commercial result</CardTitle></CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="grid grid-cols-3 gap-2 text-center">
                  <div className="rounded-lg bg-slate-50 p-2">
                    <div className="text-xs text-slate-400">{reverse ? "Initial lowest" : "Initial highest"}</div>
                    <div className="font-bold">{money(snap.result.initial_lowest)}</div>
                  </div>
                  <div className="rounded-lg bg-slate-50 p-2">
                    <div className="text-xs text-slate-400">Final</div>
                    <div className="font-bold">{money(snap.result.final_lowest)}</div>
                  </div>
                  <div className="rounded-lg bg-slate-50 p-2">
                    <div className="text-xs text-slate-400">{reverse ? "Savings" : "Premium"}</div>
                    <div className="font-bold">{snap.result.savings_amount ? `${money(snap.result.savings_amount)} (${snap.result.savings_pct}%)` : "-"}</div>
                  </div>
                </div>
                <table className="w-full text-left text-xs">
                  <thead><tr className="border-b border-slate-200 text-slate-400">
                    <th className="py-1.5 pr-2">Rank</th><th className="py-1.5 pr-2">Bidder</th><th className="py-1.5 pr-2">Company</th><th className="py-1.5 text-right">Amount</th>
                  </tr></thead>
                  <tbody>
                    {snap.result.ranking.map((r) => (
                      <tr key={r.anonymous_id} className="border-b border-slate-100 last:border-0">
                        <td className="py-1.5 pr-2 font-bold">{String(r.rank)}</td>
                        <td className="py-1.5 pr-2 font-mono">{r.anonymous_id}</td>
                        <td className="py-1.5 pr-2">{snap.participants.find((p) => p.anonymous_id === r.anonymous_id)?.company || "-"}</td>
                        <td className="py-1.5 text-right font-medium">{money(r.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {snap.result.tie && <Alert variant="warning" title="Joint (tied) price">Tender-specific tie resolution is required - ties are never broken randomly.</Alert>}
                <p className="text-xs text-slate-500">Status: {snap.result.status} · {snap.result.accepted_bid_count} accepted bids. The result hash is on the tamper-evident ledger (see Integrity).</p>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-1.5 text-sm"><ShieldCheck size={14} className="text-emerald-700" /> Integrity & risk signals</CardTitle>
                <CardDescription>Bid-hash chain, audit coverage and deterministic collusion indicators (advisory - the rule engine decides).</CardDescription>
              </div>
              <Button variant="ghost" size="sm" onClick={loadIntegrity}>Load</Button>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              {integrity ? (
                <>
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div className={`rounded-lg p-2 ${integrity.bid_chain.valid ? "bg-emerald-50" : "bg-red-50"}`}>
                      <div className="text-xs text-slate-500">Bid hash chain</div>
                      <div className="font-bold">{integrity.bid_chain.valid ? "VALID" : `BROKEN @ #${integrity.bid_chain.broken_at_sequence}`}</div>
                      <div className="text-xs text-slate-400">{integrity.bid_chain.bids_checked} bids verified</div>
                    </div>
                    <div className={`rounded-lg p-2 ${integrity.audit.audit_chain_valid ? "bg-emerald-50" : "bg-red-50"}`}>
                      <div className="text-xs text-slate-500">Audit chain</div>
                      <div className="font-bold">{integrity.audit.audit_chain_valid ? "VALID" : "INVALID"}</div>
                      <div className="text-xs text-slate-400">{integrity.audit.auction_events} auction events</div>
                    </div>
                    <div className={`rounded-lg p-2 ${integrity.collusion_signals.cartel_detected ? "bg-red-50" : "bg-emerald-50"}`}>
                      <div className="text-xs text-slate-500">Collusion risk</div>
                      <div className="font-bold">{integrity.collusion_signals.risk_level}</div>
                      <div className="text-xs text-slate-400">{integrity.collusion_signals.bidders_analyzed} bidders</div>
                    </div>
                  </div>
                  {integrity.shared_bid_ips.length > 0 && (
                    <Alert variant="warning" title="Shared source network during bidding">
                      {integrity.shared_bid_ips.map((s) => `${s.masked_ip} → ${s.participants.join(", ")}`).join(" · ")}
                    </Alert>
                  )}
                  {integrity.collusion_signals.indicators.length > 0 && (
                    <ul className="space-y-1 text-xs text-slate-600">
                      {integrity.collusion_signals.indicators.map((ind, i) => (
                        <li key={i}>• <b>{ind.signal}</b> - {ind.bidder_a} ↔ {ind.bidder_b}: {ind.evidence} <span className="text-slate-400">(weight {ind.weight})</span></li>
                      ))}
                    </ul>
                  )}
                  <p className="text-xs text-slate-400">{integrity.collusion_signals.note}</p>
                </>
              ) : (
                <p className="text-xs text-slate-400">Load to verify the bid-hash chain and review risk signals.</p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <Button variant="outline" onClick={() => downloadFile(`/officer/auctions/${id}/report`, `${snap.auction_no}-report.pdf`)}>
          <Download size={14} /> Download auction report (PDF)
        </Button>
      </div>
    </div>
  );
}
