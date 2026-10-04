"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Bookmark, Landmark, Search } from "lucide-react";
import { api } from "@/lib/api";
import type { TenderCard } from "@/lib/types";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge, eligibilityVariant, eligibilityLabel } from "@/components/ui/badge";
import { Input, Select } from "@/components/ui/fields";
import { Alert } from "@/components/ui/alert";
import { Loading, EligibilityBadge } from "@/components/app/widgets";
import { fmtDate, formatINR } from "@/lib/utils";

export default function TendersPage() {
  const t = useTranslations();
  const [tenders, setTenders] = useState<TenderCard[] | null>(null);
  const [q, setQ] = useState("");
  const [district, setDistrict] = useState("");
  const [category, setCategory] = useState("");
  const [msme, setMsme] = useState("");
  const [onlyEligible, setOnlyEligible] = useState(false);

  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (district) params.set("district", district);
    if (category) params.set("category", category);
    if (msme === "true") params.set("msme", "true");
    if (msme === "false") params.set("msme", "false");
    api<{ tenders: TenderCard[] }>(`/bidder/tenders?${params.toString()}`).then((d) => {
      setTenders(onlyEligible ? d.tenders.filter((x) => x.eligibility_badge === "ELIGIBLE") : d.tenders);
    });
  }, [q, district, category, msme, onlyEligible]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleBookmark = async (id: string, current: boolean) => {
    await api(`/bidder/tenders/${id}/bookmark`, { method: "POST", body: JSON.stringify({ action: current ? "remove" : "add" }) });
    load();
  };

  if (!tenders) return <Loading />;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-navy-900">{t("nav.tenders")}</h1>
        <p className="text-sm text-slate-500">CPCL e-procurement - {tenders.length} open tenders</p>
      </div>

      {tenders.some((x) => x.eligibility_badge === "BLACKLISTED") && (
        <Alert
          variant="critical"
          title="You are on the active debarment (blacklist) register"
          className="mb-4"
        >
          <p>
            An active debarment record is on file for your entity. Application is
            <b> disabled for all tenders, in every department and domain</b>, until the
            debarment lapses or is formally lifted. The register entry and the order
            reference are shown on each tender card.
          </p>
        </Alert>
      )}

      <Card className="p-4">
        <div className="grid gap-3 md:grid-cols-5">
          <div className="md:col-span-2">
            <div className="relative">
              <Search size={15} className="absolute left-3 top-2.5 text-slate-400" />
              <Input className="pl-9" placeholder={t("common.searchTenders")} value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          </div>
          <Select value={district} onChange={(e) => setDistrict(e.target.value)}>
            <option value="">{t("common.district")}: All</option>
            {["Belagavi", "Hubli-Dharwad", "Kolar", "Udupi", "Vijayapura", "Dakshina Kannada"].map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </Select>
          <Select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">{t("common.category")}: All</option>
            {["IT Hardware", "Stationery", "Civil Works", "Furniture", "Electrical", "Medical"].map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </Select>
          <Select value={msme} onChange={(e) => setMsme(e.target.value)}>
            <option value="">MSME preference: Any</option>
            <option value="true">MSME only</option>
            <option value="false">Non-MSME</option>
          </Select>
        </div>
        <label className="mt-3 inline-flex cursor-pointer items-center gap-2 text-sm text-slate-700">
          <input type="checkbox" checked={onlyEligible} onChange={(e) => setOnlyEligible(e.target.checked)} />
          {t("bidder.eligibleTenders")} only
        </label>
      </Card>

      {tenders.length === 0 ? (
        <Card className="p-10 text-center text-sm text-slate-500">{t("common.noData")}</Card>
      ) : (
        <div className="space-y-3">
          {tenders.map((td) => (
            <Card key={td.id} className="p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Landmark size={16} className="text-slate-400" />
                    <Link href={`/bidder/tenders/${td.id}`} className="text-sm font-semibold text-navy-900 hover:underline">
                      {td.title}
                    </Link>
                    <Badge variant={eligibilityVariant(td.eligibility_badge)}>
                      {eligibilityLabel(td.eligibility_badge)}
                    </Badge>
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {td.tender_no} · {td.organization}, {td.department} · {td.district}, {td.state} · {td.category}
                  </div>
                  {td.apply_allowed === false && td.apply_blocked_reasons?.[0] && (
                    <div className="mt-1 text-xs text-critical-700">
                      <b>{td.eligibility_badge === "BLACKLISTED" ? "Application disabled - " : "Not applicable yet - "}</b>
                      {td.apply_blocked_reasons[0]}
                    </div>
                  )}
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {td.msme_preference && <Badge variant="slate">MSME Preference</Badge>}
                    {td.make_in_india_required && <Badge variant="slate">Make in India</Badge>}
                    {td.startup_eligible && <Badge variant="slate">Startup Eligible</Badge>}
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-right">
                    <div className="text-sm font-bold text-navy-900">{formatINR(td.value_max ?? td.value_min ?? 0)}</div>
                    <div className="text-xs text-slate-500">
                      {td.value_min ? `${formatINR(td.value_min)} – ` : ""}
                      {t("common.daysLeft", { n: td.days_left })}
                    </div>
                  </div>
                  <button
                    onClick={() => toggleBookmark(td.id, td.bookmarked)}
                    className="rounded-md border border-slate-200 p-2 hover:bg-slate-50"
                    aria-label={td.bookmarked ? t("common.removeBookmark") : t("common.bookmark")}
                  >
                    <Bookmark size={15} className={td.bookmarked ? "fill-navy-900 text-navy-900" : "text-slate-400"} />
                  </button>
                  <Link href={`/bidder/tenders/${td.id}`}>
                    <Button size="sm" variant="secondary">{t("common.viewTender")}</Button>
                  </Link>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
