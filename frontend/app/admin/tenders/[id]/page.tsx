"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Lock, Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Field, Input, Select } from "@/components/ui/fields";
import { Loading } from "@/components/app/widgets";
import { fmtDate, formatINR } from "@/lib/utils";

interface Rule {
  rule_key: string;
  label: string;
  source: string;
  mandatory: boolean;
  condition_type: string;
  condition_value: string | null;
  weight: number;
  failure_action: string;
  allows_manual_review: boolean;
  hard_disqualification: boolean;
  enabled: boolean;
  sort_order: number;
}

interface CorrigendumRow {
  seq_no: number;
  title: string;
  summary: string;
  changes: Array<{ clause: string; old: string; new: string; description: string }>;
  affects_rules: boolean;
  deadline_extended_to: string | null;
  published_at: string | null;
}

interface Tender {
  id: string;
  tender_no: string;
  title: string;
  department: string;
  organization: string;
  district: string;
  state: string;
  category: string | null;
  status: string;
  close_date: string;
  value_min: number | null;
  value_max: number | null;
  required_document_types: string[];
  rules_version: number;
  rules: Rule[];
  corrigenda?: CorrigendumRow[];
}

const CONDITION_TYPES = [
  ["is_active", "Source registration is active"],
  ["valid_unexpired", "Document/registration valid & unexpired"],
  ["min_turnover", "Min annual turnover (condition value = amount)"],
  ["has_document", "Required document in vault (value = doc type)"],
  ["has_authorization", "OEM authorization present"],
  ["not_blacklisted", "Not blacklisted/debarred (hard rule)"],
  ["name_match", "Name match profile↔source (value = profile.field:SOURCE.field)"],
  ["filing_current", "ITR/GST filing current at source"],
  ["registration_required", "Registration must exist at source"],
  ["min_local_content", "Min local content % (value = percent)"],
  ["startup_certified", "Startup India certified"],
  ["names_consistent", "Names consistent across documents (cross-doc check)"],
  ["gst_state_match", "GSTIN state matches declared state"],
  ["pan_gst_match", "PAN embedded in GSTIN matches profile PAN"],
  ["turnover_gst_match", "Turnover ≥ 10% of GST 1-yr turnover (value = % threshold)"],
];

const SOURCES = ["UDYAM", "GST", "PAN", "MCA21", "EPFO", "ESIC", "STARTUP_INDIA", "NSIC", "BIS", "BLACKLIST", "PROFILE", "DOCUMENT", "LOCAL_CONTENT"];

interface CollusionIndicator {
  bidder_a: string;
  bidder_b: string;
  signal: string;
  weight: number;
  evidence: string;
}
interface CollusionResult {
  tender_id: string;
  bidders_analyzed: number;
  cartel_detected: boolean;
  risk_level: "NONE" | "LOW" | "MEDIUM" | "HIGH";
  collusion_clusters: string[][];
  indicators: CollusionIndicator[];
  excluded_common: Array<{ signal: string; value: string; bidders: number; reason: string }>;
  note: string;
}

export default function AdminTenderDetailPage() {
  const t = useTranslations();
  const params = useParams<{ id: string }>();
  const [d, setD] = useState<Tender | null>(null);
  const [rules, setRules] = useState<Rule[]>([]);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [corr, setCorr] = useState({ title: "", summary: "", affects_rules: true, deadline: "", changes: [] as Array<{ clause: string; old: string; new: string; description: string }> });
  const [collusion, setCollusion] = useState<CollusionResult | null>(null);
  const [collusionBusy, setCollusionBusy] = useState(false);

  const runCollusion = useCallback(async () => {
    setCollusionBusy(true);
    setMsg(null);
    try {
      const res = await api<CollusionResult>(`/collusion/tenders/${params.id}`);
      setCollusion(res);
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setCollusionBusy(false);
    }
  }, [params.id]);

  const load = useCallback(() => {
    api<Tender>(`/admin/tenders/${params.id}`).then((td) => {
      setD(td);
      setRules([...(td.rules ?? [])].sort((a, b) => a.sort_order - b.sort_order));
    });
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const setRule = (i: number, patch: Partial<Rule>) => {
    setRules(rules.map((r, x) => (x === i ? { ...r, ...patch } : r)));
  };

  const addRule = () => {
    setRules([
      ...rules,
      {
        rule_key: `custom_rule_${rules.length + 1}`,
        label: "New rule",
        source: "UDYAM",
        mandatory: true,
        condition_type: "is_active",
        condition_value: null,
        weight: 10,
        failure_action: "FAIL",
        allows_manual_review: false,
        hard_disqualification: false,
        enabled: true,
        sort_order: rules.length,
      },
    ]);
  };

  const removeRule = (i: number) => {
    setRules(rules.filter((_, x) => x !== i));
  };

  const save = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ message: string; tender: Tender }>(`/admin/tenders/${params.id}/rules`, {
        method: "PUT",
        body: JSON.stringify(rules),
      });
      setMsg({ ok: true, text: r.message });
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail ?? String(e) });
    } finally {
      setBusy(false);
    }
  };

  const act = async (path: string) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ message: string }>(`/admin/tenders/${params.id}${path}`, { method: "POST" });
      setMsg({ ok: true, text: r.message });
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail ?? String(e) });
    } finally {
      setBusy(false);
    }
  };

  const publishCorr = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ message: string }>(`/admin/tenders/${params.id}/corrigenda`, {
        method: "POST",
        body: JSON.stringify({
          title: corr.title,
          summary: corr.summary,
          changes: corr.changes.filter((c) => c.clause || c.old || c.new),
          affects_rules: corr.affects_rules,
          deadline_extended_to: corr.deadline || null,
        }),
      });
      setMsg({ ok: true, text: r.message });
      setCorr({ title: "", summary: "", affects_rules: true, deadline: "", changes: [] });
      load();
    } catch (e: any) {
      setMsg({ ok: false, text: e?.detail ?? String(e) });
    } finally {
      setBusy(false);
    }
  };

  if (!d) return <Loading />;
  const editable = d.status !== "CLOSED";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/admin/tenders" className="text-sm text-navy-700 hover:underline">← {t("nav.tenderMgmt")}</Link>
          <h1 className="mt-1 text-xl font-bold text-navy-900">{d.tender_no} - {d.title}</h1>
          <p className="text-sm text-slate-500">
            {d.department}, {d.organization} · {d.district}, {d.state} · {d.category} · close {fmtDate(d.close_date)} ·
            {d.value_max ? ` ${formatINR(d.value_max)}` : ""}
          </p>
          <div className="mt-1 flex gap-2">
            <Badge variant={d.status === "PUBLISHED" ? "success" : d.status === "CLOSED" ? "slate" : "warning"}>{d.status}</Badge>
            <Badge variant="slate">Rules v{d.rules_version}</Badge>
          </div>
        </div>
        <div className="flex gap-2">
          {d.status === "DRAFT" && (
            <Button variant="secondary" onClick={() => act("/publish")} loading={busy}>
              {t("admin.publish")}
            </Button>
          )}
          {d.status === "PUBLISHED" && (
            <Button variant="outline" onClick={() => act("/close")} loading={busy}>
              {t("admin.closeTender")}
            </Button>
          )}
        </div>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle>{t("admin.collusionTitle")}</CardTitle>
            <CardDescription>{t("admin.collusionDesc")}</CardDescription>
          </div>
          <Button variant="outline" onClick={runCollusion} loading={collusionBusy}>
            {t("admin.collusionRun")}
          </Button>
        </CardHeader>
        <CardContent>
          {!collusion && !collusionBusy && <p className="text-sm text-slate-500">{t("admin.collusionEmpty")}</p>}
          {collusion && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <Badge
                  variant={
                    collusion.risk_level === "HIGH"
                      ? "danger"
                      : collusion.risk_level === "MEDIUM" || collusion.risk_level === "LOW"
                        ? "warning"
                        : "success"
                  }
                >
                  {collusion.risk_level}
                </Badge>
                {collusion.cartel_detected && <Badge variant="danger">CARTEL RISK</Badge>}
                <span className="text-sm text-slate-500">
                  {t("admin.collusionBidders", { n: collusion.bidders_analyzed })}
                </span>
              </div>
              {collusion.collusion_clusters.length > 0 && (
                <div className="space-y-1">
                  <span className="label">{t("admin.collusionClusters")}</span>
                  {collusion.collusion_clusters.map((c, i) => (
                    <div key={i} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs">
                      {c.join("  ↔  ")}
                    </div>
                  ))}
                </div>
              )}
              {collusion.indicators.length === 0 ? (
                <p className="text-sm text-emerald-600">{t("admin.collusionClean")}</p>
              ) : (
                <div className="overflow-x-auto rounded-md border">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50 text-left">
                      <tr>
                        <th className="px-3 py-2">{t("admin.collusionBids")}</th>
                        <th className="px-3 py-2">{t("admin.collusionType")}</th>
                        <th className="px-3 py-2">{t("admin.collusionShared")}</th>
                        <th className="px-3 py-2 text-right">{t("admin.collusionWeight")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {collusion.indicators.map((ind, i) => (
                        <tr key={i} className="border-t">
                          <td className="px-3 py-2">
                            {ind.bidder_a} ↔ {ind.bidder_b}
                          </td>
                          <td className="px-3 py-2 font-mono text-xs">{ind.signal}</td>
                          <td className="px-3 py-2 text-xs">{ind.evidence}</td>
                          <td className="px-3 py-2 text-right">{ind.weight.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {collusion.excluded_common.length > 0 && (
                <div className="space-y-1">
                  <span className="label">{t("admin.collusionExcluded")}</span>
                  <p className="text-xs text-slate-500">
                    {collusion.excluded_common
                      .map((e) => `${e.signal} (${e.bidders} bidders) - ${e.reason}`)
                      .join("; ")}
                  </p>
                </div>
              )}
              <p className="text-xs text-slate-500">{collusion.note}</p>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("admin.ruleBuilderTitle")}</CardTitle>
          <CardDescription>{t("admin.ruleHint")}</CardDescription>
        </CardHeader>
        <CardContent>
          {!editable && <Alert variant="warning" className="mb-4">Tender is closed - rules are locked.</Alert>}
          <div className="space-y-3">
            {rules.map((r, i) => {
              const locked = r.rule_key === "not_blacklisted";
              return (
                <div key={i} className="grid gap-3 rounded-md border border-slate-200 p-3 lg:grid-cols-12">
                  <div className="lg:col-span-3">
                    <span className="label">{t("admin.ruleKey")}</span>
                    <Input disabled={locked} value={r.rule_key} onChange={(e) => setRule(i, { rule_key: e.target.value })} />
                  </div>
                  <div className="lg:col-span-3">
                    <span className="label">{t("admin.ruleLabel")}</span>
                    <Input disabled={locked} value={r.label} onChange={(e) => setRule(i, { label: e.target.value })} />
                  </div>
                  <div className="lg:col-span-2">
                    <span className="label">{t("admin.conditionType")}</span>
                    <Select disabled={locked} value={r.condition_type} onChange={(e) => setRule(i, { condition_type: e.target.value })}>
                      {CONDITION_TYPES.map(([v, l]) => (
                        <option key={v} value={v}>{l}</option>
                      ))}
                    </Select>
                  </div>
                  <div className="lg:col-span-2">
                    <span className="label">{t("admin.ruleSource")}</span>
                    <Select disabled={locked} value={r.source} onChange={(e) => setRule(i, { source: e.target.value })}>
                      {SOURCES.map((s) => (
                        <option key={s}>{s}</option>
                      ))}
                    </Select>
                  </div>
                  <div className="lg:col-span-2">
                    <span className="label">{t("admin.conditionValue")}</span>
                    <Input disabled={locked} value={r.condition_value ?? ""} onChange={(e) => setRule(i, { condition_value: e.target.value || null })} placeholder="e.g. 20000000" />
                  </div>
                  <div className="lg:col-span-2">
                    <span className="label">{t("admin.weight")}</span>
                    <Input type="number" min={1} max={100} disabled={locked} value={r.weight} onChange={(e) => setRule(i, { weight: Number(e.target.value) })} />
                  </div>
                  <div className="lg:col-span-2">
                    <span className="label">{t("admin.failureAction")}</span>
                    <Select disabled={locked} value={r.failure_action} onChange={(e) => setRule(i, { failure_action: e.target.value })}>
                      <option value="FAIL">FAIL</option>
                      <option value="FLAG">FLAG</option>
                    </Select>
                  </div>
                  <div className="flex items-end gap-4 lg:col-span-8">
                    <label className="flex items-center gap-1.5 text-sm">
                      <input type="checkbox" disabled={locked} checked={r.mandatory} onChange={(e) => setRule(i, { mandatory: e.target.checked })} />
                      {t("admin.mandatory")}
                    </label>
                    <label className="flex items-center gap-1.5 text-sm">
                      <input type="checkbox" disabled={locked} checked={r.allows_manual_review} onChange={(e) => setRule(i, { allows_manual_review: e.target.checked })} />
                      {t("admin.manualReviewAllow")}
                    </label>
                    <label className="flex items-center gap-1.5 text-sm">
                      <input type="checkbox" disabled checked={r.hard_disqualification || locked} onChange={() => {}} />
                      {t("admin.hardDisqualify")}
                      {locked && <Lock size={12} className="text-critical-600" />}
                    </label>
                    <label className="flex items-center gap-1.5 text-sm">
                      <input type="checkbox" checked={r.enabled} onChange={(e) => setRule(i, { enabled: e.target.checked })} disabled={locked} />
                      Enabled
                    </label>
                    <button
                      className="ml-auto rounded-md p-1.5 text-slate-400 hover:bg-slate-100 disabled:opacity-30"
                      onClick={() => removeRule(i)}
                      disabled={locked}
                      title={locked ? "Hard compliance rule - cannot be removed" : "Remove rule"}
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-4 flex gap-2">
            <Button variant="outline" size="sm" onClick={addRule} disabled={!editable}>
              <Plus size={14} /> Add rule
            </Button>
            <Button variant="secondary" size="sm" onClick={save} loading={busy} disabled={!editable}>
              {t("admin.saveRules")}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("admin.corrigendaTitle")}</CardTitle>
          <CardDescription>{t("admin.corrigendaDesc")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {(d.corrigenda ?? []).length === 0 && (
            <p className="text-sm text-slate-500">{t("admin.noCorrigenda")}</p>
          )}
          {(d.corrigenda ?? []).map((c) => (
            <div key={c.seq_no} className="rounded-md border border-slate-200 bg-slate-50/60 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-semibold text-slate-800">{c.seq_no}. {c.title}</span>
                <div className="flex items-center gap-2">
                  <Badge variant={c.affects_rules ? "warning" : "slate"}>{c.affects_rules ? t("admin.corrAffectsRules") : "Info only"}</Badge>
                  <span className="text-xs text-slate-500">{c.published_at ?? ""}</span>
                </div>
              </div>
              <p className="mt-1 text-xs text-slate-600">{c.summary}</p>
              {c.changes && c.changes.length > 0 && (
                <table className="mt-2 w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-slate-200 text-[11px] uppercase tracking-wide text-slate-500">
                      <th className="py-1 pr-3">{t("bidder.clause")}</th>
                      <th className="py-1 pr-3">{t("bidder.was")}</th>
                      <th className="py-1 pr-3">{t("bidder.now")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {c.changes.map((ch, i) => (
                      <tr key={i}>
                        <td className="py-1.5 pr-3 font-medium text-slate-700">{ch.clause}</td>
                        <td className="py-1.5 pr-3 text-slate-500 line-through">{ch.old}</td>
                        <td className="py-1.5 font-medium text-slate-800">{ch.new}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {c.deadline_extended_to && (
                <p className="mt-2 text-xs font-medium text-amber-700">{t("bidder.deadlineExtended")}: {fmtDate(c.deadline_extended_to)}</p>
              )}
            </div>
          ))}

          <div className="rounded-md border border-dashed border-slate-300 p-3">
            <div className="label">{t("admin.corrigendumPublish")}</div>
            <div className="mt-2 grid gap-3 sm:grid-cols-3">
              <Field label={t("admin.corrTitle")} required>
                <Input value={corr.title} onChange={(e) => setCorr({ ...corr, title: e.target.value })} placeholder="e.g. Corrigendum #2 - Specification update" />
              </Field>
              <Field label={t("admin.corrDeadlineExt")} hint="Optional - extends the submission deadline">
                <Input type="date" value={corr.deadline} onChange={(e) => setCorr({ ...corr, deadline: e.target.value })} />
              </Field>
              <div className="flex items-end pb-1">
                <label className="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" checked={corr.affects_rules} onChange={(e) => setCorr({ ...corr, affects_rules: e.target.checked })} />
                  {t("admin.corrAffectsRules")}
                </label>
              </div>
            </div>
            <div className="mt-3">
              <Field label={t("admin.corrSummary")} required>
                <Input value={corr.summary} onChange={(e) => setCorr({ ...corr, summary: e.target.value })} placeholder="What changed and why (bidders see this)" />
              </Field>
            </div>
            <div className="mt-3 space-y-2">
              <div className="flex items-center justify-between">
                <span className="label">{t("admin.corrChanges")}</span>
                <Button size="sm" variant="ghost" onClick={() => setCorr({ ...corr, changes: [...corr.changes, { clause: "", old: "", new: "", description: "" }] })}>
                  <Plus size={13} /> {t("admin.corrAddChange")}
                </Button>
              </div>
              {corr.changes.map((ch, i) => (
                <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1fr_1fr_1fr_auto]">
                  <Input placeholder="Clause" value={ch.clause} onChange={(e) => setCorr({ ...corr, changes: corr.changes.map((x, xi) => (xi === i ? { ...x, clause: e.target.value } : x)) })} />
                  <Input placeholder="Old" value={ch.old} onChange={(e) => setCorr({ ...corr, changes: corr.changes.map((x, xi) => (xi === i ? { ...x, old: e.target.value } : x)) })} />
                  <Input placeholder="New" value={ch.new} onChange={(e) => setCorr({ ...corr, changes: corr.changes.map((x, xi) => (xi === i ? { ...x, new: e.target.value } : x)) })} />
                  <Input placeholder="Description" value={ch.description} onChange={(e) => setCorr({ ...corr, changes: corr.changes.map((x, xi) => (xi === i ? { ...x, description: e.target.value } : x)) })} />
                  <button className="rounded-md p-1.5 text-slate-400 hover:bg-slate-100" onClick={() => setCorr({ ...corr, changes: corr.changes.filter((_, xi) => xi !== i) })}>
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
            </div>
            <div className="mt-3">
              <Button variant="secondary" size="sm" onClick={publishCorr} loading={busy} disabled={!corr.title.trim() || !corr.summary.trim()}>
                {t("admin.corrigendumPublish")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Required documents (default)</CardTitle></CardHeader>
        <CardContent className="flex flex-wrap gap-1.5">
          {(d.required_document_types ?? []).map((x) => (
            <Badge key={x} variant="slate">{x.replace(/_/g, " ").toLowerCase()}</Badge>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
