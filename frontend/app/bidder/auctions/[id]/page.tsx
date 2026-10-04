"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { ArrowDownRight, ArrowUpRight, Clock, PauseCircle, Radio } from "lucide-react";
import { api } from "@/lib/api";
import type { AuctionSnapshot } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/fields";
import { Loading } from "@/components/app/widgets";
import { formatINR } from "@/lib/utils";

const STATUS_CLS: Record<string, string> = {
  SCHEDULED: "bg-sky-100 text-sky-800",
  LIVE: "bg-emerald-100 text-emerald-800",
  EXTENDED: "bg-amber-100 text-amber-800",
  PAUSED: "bg-amber-100 text-amber-800",
  RESULT_PENDING: "bg-indigo-100 text-indigo-800",
  COMPLETED: "bg-emerald-100 text-emerald-800",
  CANCELLED: "bg-red-100 text-red-700",
};

function fmtClock(s: number | null): string {
  if (s == null) return "-";
  const m = Math.floor(s / 60), sec = s % 60;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export default function AuctionRoomPage() {
  const { id } = useParams<{ id: string }>();
  const [snap, setSnap] = useState<AuctionSnapshot | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [amount, setAmount] = useState("");
  const [bidErr, setBidErr] = useState<string | null>(null);
  const [bidOk, setBidOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const idemRef = useRef<string | null>(null);

  const load = useCallback(() => {
    api<AuctionSnapshot>(`/bidder/auctions/${id}`)
      .then((d) => { setSnap(d); setErr(null); })
      .catch((e) => setErr(String(e.detail?.message || e.message || e)));
  }, [id]);

  useEffect(() => {
    load();
    const iv = setInterval(load, 3000);
    return () => clearInterval(iv);
  }, [load]);

  const reverse = snap?.auction_type !== "FORWARD";
  const live = snap && (snap.status === "LIVE" || snap.status === "EXTENDED");
  const eligible = snap?.your_participation?.ra_eligible === true;

  const hint = useMemo(() => {
    if (!snap || !eligible) return null;
    const inc = parseFloat(snap.min_increment || "0");
    const lead = snap.leading ? parseFloat(snap.leading.amount) : null;
    const own = snap.your_participation?.current_price ? parseFloat(snap.your_participation.current_price) : null;
    if (reverse) {
      if (lead != null) return `Valid bids: below your current price and at most ${formatINR(lead - inc)} (leading ${formatINR(lead)} − ${formatINR(inc)} increment)`;
      return `Minimum increment ${formatINR(inc)} below the leading price`;
    }
    if (lead != null) return `Valid bids: above the leading price, at least ${formatINR(lead + inc)} (leading ${formatINR(lead)} + ${formatINR(inc)} increment)`;
    return `Minimum increment ${formatINR(inc)} above the leading price`;
  }, [snap, eligible, reverse]);

  const submit = async () => {
    setBidErr(null); setBidOk(null);
    const val = parseFloat(amount);
    if (!Number.isFinite(val) || val <= 0) { setBidErr("Enter a valid amount."); return; }
    if (!idemRef.current) idemRef.current = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`);
    setBusy(true);
    try {
      const r = await api<{ bid: { sequence_number: number; status: string; amount: string }; duplicate: boolean }>(
        `/bidder/auctions/${id}/bids`,
        { method: "POST", body: JSON.stringify({ amount: val.toFixed(2), idempotency_key: idemRef.current }) },
      );
      idemRef.current = null;
      setAmount("");
      setBidOk(r.duplicate ? "Duplicate submission ignored - your original bid stands." : `Bid #${r.bid.sequence_number} accepted: ${formatINR(parseFloat(r.bid.amount))}`);
      load();
    } catch (e: unknown) {
      const d = (e as { detail?: { message?: string } })?.detail;
      setBidErr(d?.message || "Bid rejected - please check the amount and retry.");
    } finally {
      setBusy(false);
    }
  };

  if (err) return <Alert variant="critical" title="Could not load the auction room">{err}</Alert>;
  if (!snap) return <Loading />;

  const yp = snap.your_participation;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            {reverse
              ? <ArrowDownRight size={18} className="text-red-700" />
              : <ArrowUpRight size={18} className="text-emerald-700" />}
            <h1 className="text-xl font-bold text-navy-900">{snap.auction_no}</h1>
            <Badge className={STATUS_CLS[snap.status] || "bg-slate-100 text-slate-700"}>{snap.status}</Badge>
            {live && snap.extension_count > 0 && <Badge className="bg-amber-100 text-amber-800">extended ×{snap.extension_count}</Badge>}
          </div>
          <p className="text-sm text-slate-500">
            {snap.title} · {snap.auction_type === "FORWARD" ? "forward e-auction - bids increase" : "reverse auction - bids decrease"}
          </p>
        </div>
        {live && (
          <div className="flex items-center gap-2 rounded-lg bg-navy-900 px-4 py-2 text-white">
            <Clock size={16} />
            <span className="font-mono text-lg font-semibold tabular-nums">{fmtClock(snap.seconds_remaining)}</span>
          </div>
        )}
      </div>

      {snap.status === "PAUSED" && (
        <Alert variant="warning" title="Auction paused">Bidding is temporarily suspended by the procurement authority.</Alert>
      )}
      {snap.status === "SCHEDULED" && (
        <Alert variant="info" title="Auction scheduled">
          Starts {snap.scheduled_start_at ? new Date(snap.scheduled_start_at + "Z").toLocaleString() : "soon"}. You will be able to bid once it goes live.
        </Alert>
      )}
      {snap.status === "CANCELLED" && <Alert variant="critical" title="Auction cancelled">This auction was cancelled by the procurement authority.</Alert>}

      {/* your participation */}
      {yp && (
        <Card className="p-4">
          <h2 className="mb-2 text-sm font-semibold text-navy-900">Your participation</h2>
          {!yp.ra_eligible ? (
            <p className="text-sm text-red-700">
              You are <b>not qualified</b> for this auction{yp.qualification_reason ? ` - ${yp.qualification_reason}` : ""}. You cannot place bids.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
              <div>
                <div className="text-xs text-slate-400">Your identifier</div>
                <div className="font-mono text-xs font-semibold">{yp.anonymous_id}</div>
              </div>
              <div>
                <div className="text-xs text-slate-400">Your current price</div>
                <div className="font-semibold">{yp.current_price ? formatINR(parseFloat(yp.current_price)) : "-"}</div>
              </div>
              <div>
                <div className="text-xs text-slate-400">Bids placed</div>
                <div className="font-semibold">{yp.bids?.length ?? 0}</div>
              </div>
              <div>
                <div className="text-xs text-slate-400">Leading price</div>
                <div className="font-semibold">{snap.leading ? formatINR(parseFloat(snap.leading.amount)) : "-"}</div>
              </div>
            </div>
          )}
        </Card>
      )}

      {/* leading + bid form */}
      {live && eligible && (
        <div className="grid gap-4 md:grid-cols-2">
          <Card className="p-4">
            <h2 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-navy-900">
              <Radio size={14} className="text-emerald-700" /> Market leader
            </h2>
            {snap.leading ? (
              <>
                <div className="text-3xl font-bold text-navy-900">{formatINR(parseFloat(snap.leading.amount))}</div>
                <div className="mt-1 font-mono text-xs text-slate-500">{snap.leading.anonymous_id}{snap.leading.sequence_number ? ` · bid #${snap.leading.sequence_number}` : " · opening price"}</div>
              </>
            ) : (
              <p className="text-sm text-slate-400">No revised bids yet - the opening price is the best qualifying sealed quotation.</p>
            )}
            {hint && <p className="mt-3 text-xs text-slate-500">{hint}</p>}
          </Card>

          <Card className="p-4">
            <h2 className="mb-2 text-sm font-semibold text-navy-900">Place a {reverse ? "lower" : "higher"} bid</h2>
            <div className="flex gap-2">
              <Input
                type="number"
                min={1}
                step="0.01"
                placeholder="Amount in INR"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                className="flex-1"
                disabled={busy}
              />
              <Button onClick={submit} disabled={busy}>{busy ? "Placing…" : "Bid"}</Button>
            </div>
            {bidErr && <Alert variant="critical" className="mt-3" title="Bid rejected">{bidErr}</Alert>}
            {bidOk && <Alert variant="success" className="mt-3" title="Bid accepted">{bidOk}</Alert>}
            <p className="mt-3 text-xs text-slate-400">
              Server-authoritative: bids are validated against the leading price, your own last price and the minimum increment. Duplicate submissions (same amount retried) are safely ignored.
            </p>
          </Card>
        </div>
      )}

      {live && !eligible && (
        <Alert variant="warning" title="Spectator view">You can watch the live state, but only qualified bidders can place bids.</Alert>
      )}

      {/* recent bids */}
      {snap.recent_bids.length > 0 && (
        <Card className="p-4">
          <h2 className="mb-2 text-sm font-semibold text-navy-900">Recent bids</h2>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-400">
                <th className="py-1.5 pr-3">#</th>
                <th className="py-1.5 pr-3">Bidder</th>
                <th className="py-1.5 pr-3 text-right">Amount</th>
                <th className="py-1.5 text-right">Server received (UTC)</th>
              </tr>
            </thead>
            <tbody>
              {snap.recent_bids.map((b) => (
                <tr key={b.sequence_number} className="border-b border-slate-100 last:border-0">
                  <td className="py-1.5 pr-3 font-mono text-xs">{b.sequence_number}</td>
                  <td className="py-1.5 pr-3 font-mono text-xs">{b.anonymous_id || "-"}</td>
                  <td className="py-1.5 pr-3 text-right font-semibold">{formatINR(parseFloat(b.amount))}</td>
                  <td className="py-1.5 text-right text-xs text-slate-500">{b.server_received_at ? new Date(b.server_received_at + "Z").toLocaleTimeString() : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {/* result */}
      {snap.result && (
        <Card className="p-4">
          <h2 className="mb-2 text-sm font-semibold text-navy-900">Commercial result</h2>
          <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
            <div>
              <div className="text-xs text-slate-400">{snap.auction_type === "FORWARD" ? "Initial highest" : "Initial lowest"}</div>
              <div className="font-semibold">{snap.result.initial_lowest ? formatINR(parseFloat(snap.result.initial_lowest)) : "-"}</div>
            </div>
            <div>
              <div className="text-xs text-slate-400">Final</div>
              <div className="font-semibold">{snap.result.final_lowest ? formatINR(parseFloat(snap.result.final_lowest)) : "-"}</div>
            </div>
            <div>
              <div className="text-xs text-slate-400">{snap.auction_type === "FORWARD" ? "Premium" : "Savings"}</div>
              <div className="font-semibold">{snap.result.savings_amount ? `${formatINR(parseFloat(snap.result.savings_amount))} (${snap.result.savings_pct}%)` : "-"}</div>
            </div>
            <div>
              <div className="text-xs text-slate-400">Result status</div>
              <div className="font-semibold">{snap.result.status}</div>
            </div>
          </div>
          {yp?.ra_eligible && snap.result.ranking.some((r) => r.anonymous_id === yp.anonymous_id) && (
            <p className="mt-3 text-sm">
              Your position: <b>{String(snap.result.ranking.find((r) => r.anonymous_id === yp.anonymous_id)!.rank)}</b> at{" "}
              {formatINR(parseFloat(snap.result.ranking.find((r) => r.anonymous_id === yp.anonymous_id)!.amount))}.
              <span className="text-slate-500"> The full ranking and award decision are made by the Competent Authority.</span>
            </p>
          )}
        </Card>
      )}
    </div>
  );
}
