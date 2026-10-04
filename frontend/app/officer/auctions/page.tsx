"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowDownRight, ArrowUpRight, Plus, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { OfficerAuction, OfficerTender } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/fields";
import { Loading } from "@/components/app/widgets";
import { fmtDateTime } from "@/lib/utils";

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

export default function OfficerAuctionsPage() {
  const t = useTranslations();
  const [auctions, setAuctions] = useState<OfficerAuction[] | null>(null);
  const [tenders, setTenders] = useState<OfficerTender[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ tender_id: "", title: "", min_increment: "", opening_reference_price: "", rule: "ALL_TECHNICALLY_QUALIFIED" });
  const [busy, setBusy] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [formOk, setFormOk] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ auctions: OfficerAuction[] }>("/officer/auctions").then((d) => setAuctions(d.auctions));
    api<{ tenders: OfficerTender[] }>("/officer/tenders").then((d) => setTenders(d.tenders));
  }, []);
  useEffect(() => { load(); }, [load]);

  const selectedTender = tenders.find((x) => x.id === form.tender_id);
  const auctionTypes: Record<string, "REVERSE" | "FORWARD"> = {
    E_BID_WITH_REVERSE_AUCTION: "REVERSE",
    FORWARD_E_AUCTION: "FORWARD",
  };
  const auctionTenders = tenders.filter((x) => auctionTypes[x.procurement_mode]);

  const create = async () => {
    setFormErr(null); setFormOk(null);
    if (!form.tender_id) { setFormErr("Select a tender."); return; }
    if (!form.min_increment || Number(form.min_increment) <= 0) { setFormErr("Enter a positive minimum increment."); return; }
    const mode = selectedTender?.procurement_mode;
    const auction_type = mode ? auctionTypes[mode] : undefined;
    if (!auction_type) { setFormErr("The selected tender is not in auction mode."); return; }
    setBusy(true);
    try {
      const r = await api<{ auction_id: string; auction_no: string; status: string }>("/officer/auctions", {
        method: "POST",
        body: JSON.stringify({
          tender_id: form.tender_id,
          auction_type,
          title: form.title || null,
          eligibility_rule: form.rule,
          min_increment: form.min_increment,
          opening_reference_price: form.opening_reference_price || null,
          auto_extend_enabled: true,
          extension_trigger_seconds: 120,
          extension_seconds: 120,
          max_extensions: 3,
          notes: "Created from the officer auction console.",
        }),
      });
      setFormOk(`Auction ${r.auction_no} created (${r.status}). Open it to schedule and start.`);
      setForm({ tender_id: "", title: "", min_increment: "", opening_reference_price: "", rule: "ALL_TECHNICALLY_QUALIFIED" });
      load();
    } catch (e: unknown) {
      const d = (e as { detail?: { message?: string; code?: string } })?.detail;
      setFormErr(`${d?.code ? d.code + " - " : ""}${d?.message || "Auction could not be created."}`);
    } finally {
      setBusy(false);
    }
  };

  if (!auctions) return <Loading />;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-navy-900">{t("nav.auctions")}</h1>
          <p className="text-sm text-slate-500">Competitive e-auctions across your assigned tenders</p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={load} className="text-slate-500"><RefreshCw size={14} /> Refresh</Button>
          <Button onClick={() => setShowCreate((v) => !v)}><Plus size={14} /> New auction</Button>
        </div>
      </div>

      {showCreate && (
        <Card className="p-4">
          <h2 className="mb-3 text-sm font-semibold text-navy-900">New auction</h2>
          <div className="grid gap-3 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">Tender (auction-mode only)</label>
              <Select value={form.tender_id} onChange={(e) => setForm({ ...form, tender_id: e.target.value })}>
                <option value="">- select tender -</option>
                {auctionTenders.map((x) => (
                  <option key={x.id} value={x.id} disabled={!x.bids_open}>
                    {x.tender_no} - {x.title}{x.bids_open ? "" : " (bids not open yet)"}
                  </option>
                ))}
              </Select>
              {selectedTender && (
                <p className="mt-1 text-xs text-slate-500">
                  {selectedTender.procurement_mode_label} → {auctionTypes[selectedTender.procurement_mode]} auction
                  {auctionTypes[selectedTender.procurement_mode] === "FORWARD"
                    ? " (bidders increase; highest valid bid is recommended)"
                    : " (bidders decrease; lowest valid price wins)"}
                </p>
              )}
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">Title (optional)</label>
              <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g. Reverse Auction - TDR-2026-010" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">Minimum increment (INR)</label>
              <Input type="number" min={1} value={form.min_increment} onChange={(e) => setForm({ ...form, min_increment: e.target.value })} placeholder="e.g. 100000" />
            </div>
            {selectedTender?.procurement_mode === "FORWARD_E_AUCTION" && (
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-600">Opening reference price (INR, optional)</label>
                <Input type="number" min={0} value={form.opening_reference_price} onChange={(e) => setForm({ ...form, opening_reference_price: e.target.value })} placeholder="Leave empty to open against the best sealed quotation" />
              </div>
            )}
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-600">Eligibility rule</label>
              <Select value={form.rule} onChange={(e) => setForm({ ...form, rule: e.target.value })}>
                <option value="ALL_TECHNICALLY_QUALIFIED">All technically qualified bidders</option>
              </Select>
            </div>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <Button onClick={create} disabled={busy}>{busy ? "Creating…" : "Create auction"}</Button>
            {formErr && <span className="text-sm text-red-700">{formErr}</span>}
            {formOk && <span className="text-sm text-emerald-700">{formOk}</span>}
          </div>
        </Card>
      )}

      <Card className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-400">
              <th className="px-4 py-2.5">Auction</th>
              <th className="px-4 py-2.5">Tender</th>
              <th className="px-4 py-2.5">Type</th>
              <th className="px-4 py-2.5">Status</th>
              <th className="px-4 py-2.5">Participants</th>
              <th className="px-4 py-2.5 text-right">Close</th>
            </tr>
          </thead>
          <tbody>
            {auctions.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-400">No auctions yet - create one from an auction-mode tender.</td></tr>
            )}
            {auctions.map((a) => (
              <tr key={a.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50">
                <td className="px-4 py-2.5">
                  <Link href={`/officer/auctions/${a.id}`} className="font-semibold text-navy-800 hover:underline">{a.auction_no}</Link>
                  <div className="text-xs text-slate-500">{a.title}</div>
                </td>
                <td className="px-4 py-2.5">{a.tender_no}</td>
                <td className="px-4 py-2.5">
                  <span className="inline-flex items-center gap-1 text-xs font-medium">
                    {a.auction_type === "FORWARD"
                      ? <><ArrowUpRight size={13} className="text-emerald-700" /> Forward (increases)</>
                      : <><ArrowDownRight size={13} className="text-red-700" /> Reverse (decreases)</>}
                  </span>
                </td>
                <td className="px-4 py-2.5"><Badge className={STATUS_CLS[a.status] || "bg-slate-100 text-slate-700"}>{a.status}</Badge></td>
                <td className="px-4 py-2.5">{a.eligible_participants}</td>
                <td className="px-4 py-2.5 text-right text-xs text-slate-500">{a.effective_end_at ? fmtDateTime(a.effective_end_at) : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
