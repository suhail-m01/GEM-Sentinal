"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowDownRight, ArrowUpRight, Clock, Gavel, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import type { MyAuctionEntry } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Loading } from "@/components/app/widgets";
import { formatINR } from "@/lib/utils";

const STATUS_CLS: Record<string, string> = {
  DRAFT: "bg-slate-100 text-slate-700",
  SCHEDULED: "bg-sky-100 text-sky-800",
  READY: "bg-sky-100 text-sky-800",
  LIVE: "bg-emerald-100 text-emerald-800",
  EXTENDED: "bg-amber-100 text-amber-800",
  PAUSED: "bg-amber-100 text-amber-800",
  CLOSED: "bg-slate-200 text-slate-700",
  RESULT_PENDING: "bg-indigo-100 text-indigo-800",
  COMPLETED: "bg-emerald-100 text-emerald-800",
  CANCELLED: "bg-red-100 text-red-700",
};

function TypeIcon({ type, size = 16 }: { type: string; size?: number }) {
  return type === "FORWARD" ? (
    <ArrowUpRight size={size} className="text-emerald-700" />
  ) : (
    <ArrowDownRight size={size} className="text-red-700" />
  );
}

export default function MyAuctionsPage() {
  const t = useTranslations();
  const [data, setData] = useState<{ upcoming: MyAuctionEntry[]; live: MyAuctionEntry[]; completed: MyAuctionEntry[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(() => {
    api<{ upcoming: MyAuctionEntry[]; live: MyAuctionEntry[]; completed: MyAuctionEntry[] }>("/bidder/auctions")
      .then(setData)
      .catch((e) => setErr(String(e.message || e)));
  }, []);

  useEffect(() => { load(); }, [load]);

  if (err) return <Alert variant="critical" title="Could not load your auctions">{err}</Alert>;
  if (!data) return <Loading />;

  const sections: { key: "upcoming" | "live" | "completed"; title: string; hint: string }[] = [
    { key: "live", title: "Live now", hint: "Enter the room to place bids" },
    { key: "upcoming", title: "Upcoming", hint: "Scheduled auctions you are participating in" },
    { key: "completed", title: "Completed", hint: "Closed auctions and their outcome" },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-navy-900">{t("nav.auctions")}</h1>
          <p className="text-sm text-slate-500">Competitive e-auctions you are participating in</p>
        </div>
        <button onClick={load} className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-navy-800">
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {sections.map(({ key, title, hint }) => (
        <section key={key}>
          <div className="mb-2 flex items-baseline gap-2">
            <h2 className="text-sm font-semibold text-navy-900">{title}</h2>
            <span className="text-xs text-slate-400">{hint} · {data[key].length}</span>
          </div>
          {data[key].length === 0 ? (
            <Card className="p-4 text-sm text-slate-400">-</Card>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {data[key].map((a) => {
                const qualified = a.your_status === "Qualified";
                return (
                  <Link key={a.auction_id} href={`/bidder/auctions/${a.auction_id}`}>
                    <Card className="h-full p-4 transition hover:border-navy-400 hover:shadow-sm">
                      <div className="flex items-start justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <TypeIcon type={a.auction_type} />
                          <div>
                            <div className="text-sm font-semibold text-navy-900">{a.auction_no}</div>
                            <div className="text-xs text-slate-500">{a.tender_no} - {a.tender_title}</div>
                          </div>
                        </div>
                        <Badge className={STATUS_CLS[a.status] || "bg-slate-100 text-slate-700"}>{a.status}</Badge>
                      </div>
                      <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
                        <div>
                          <span className="text-slate-400">Direction: </span>
                          <span className="font-medium text-slate-700">
                            {a.auction_type === "FORWARD" ? "bids increase" : "bids decrease"}
                          </span>
                        </div>
                        <div>
                          <span className="text-slate-400">Your status: </span>
                          <span className={qualified ? "font-medium text-emerald-700" : "font-medium text-red-700"}>
                            {a.your_status}
                          </span>
                        </div>
                        <div>
                          <span className="text-slate-400">Initial: </span>
                          <span className="font-medium">{a.initial_price ? formatINR(parseFloat(a.initial_price)) : "-"}</span>
                        </div>
                        <div>
                          <span className="text-slate-400">Current: </span>
                          <span className="font-medium">{a.current_price ? formatINR(parseFloat(a.current_price)) : "-"}</span>
                        </div>
                      </div>
                      {!qualified && a.qualification_reason && (
                        <p className="mt-2 text-xs text-red-600">{a.qualification_reason}</p>
                      )}
                      {a.effective_end_at && (a.status === "LIVE" || a.status === "EXTENDED" || a.status === "PAUSED") && (
                        <p className="mt-2 flex items-center gap-1 text-xs text-slate-500">
                          <Clock size={12} /> Closes {new Date(a.effective_end_at + "Z").toLocaleString()}
                        </p>
                      )}
                    </Card>
                  </Link>
                );
              })}
            </div>
          )}
        </section>
      ))}
      <p className="flex items-center gap-1.5 text-xs text-slate-400">
        <Gavel size={12} />
        In a reverse auction valid bids must be lower than the leading price (minus the minimum increment); in a forward e-auction they must be higher (plus the minimum increment). All times are server-authoritative.
      </p>
    </div>
  );
}
