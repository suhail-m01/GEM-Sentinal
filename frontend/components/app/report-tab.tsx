"use client";

import React from "react";
import { useTranslations } from "next-intl";
import { Download, ShieldCheck, ScrollText, Route, Search } from "lucide-react";
import type { VerificationReport, ExplanationDetail } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge, authenticityVariant } from "@/components/ui/badge";

const STRENGTH_META: Record<string, { label: string; cls: string }> = {
  S4: { label: "Cross-verified", cls: "bg-purple-100 text-purple-800 border-purple-300" },
  S3: { label: "Authoritative source", cls: "bg-emerald-100 text-emerald-800 border-emerald-300" },
  S2: { label: "Documentary evidence", cls: "bg-sky-100 text-sky-800 border-sky-300" },
  S1: { label: "Self-declared / format-only", cls: "bg-amber-100 text-amber-800 border-amber-300" },
  S0: { label: "No evidence", cls: "bg-red-100 text-red-800 border-red-300" },
};
import { Alert } from "@/components/ui/alert";
import { ScoreRing, RiskBadge, EligibilityBadge, ResultBadge } from "@/components/app/widgets";
import { fmtDate, fmtDateTime } from "@/lib/utils";

function XaiSection({ detail, t }: { detail: ExplanationDetail; t: (k: string) => string }) {
  const gov = detail.governance;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("officer.xaiTitle")}</CardTitle>
        <CardDescription>{t("officer.xaiDesc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {/* Executive summary */}
        <div>
          <div className="label mb-1">{t("officer.xaiExecSummary")}</div>
          <ul className="space-y-1 text-sm text-slate-700">
            {detail.executive_summary.headline.map((h, i) => (
              <li key={i}>• {h}</li>
            ))}
          </ul>
          {detail.executive_summary.counts && (
            <p className="mt-1 text-xs text-slate-500">
              {t("officer.xaiCounts")}: P {detail.executive_summary.counts.passed ?? 0} · F {detail.executive_summary.counts.failed ?? 0} ·
              V {detail.executive_summary.counts.unverified ?? 0} · N/A {detail.executive_summary.counts.not_applicable ?? 0}
            </p>
          )}
        </div>

        {/* Basis of decision */}
        {detail.basis_of_decision.length > 0 && (
          <div>
            <div className="label mb-2 flex items-center gap-1.5">
              <ShieldCheck size={13} className="text-navy-700" /> {t("officer.xaiBasis")}
            </div>
            <div className="space-y-2">
              {detail.basis_of_decision.map((b, i) => (
                <div key={i} className={`rounded-md border p-3 ${b.result === "FAIL" ? "border-red-200 bg-red-50/50" : "border-amber-200 bg-amber-50/50"}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <ResultBadge result={b.result} />
                    <span className="text-sm font-medium text-slate-800">{b.rule}</span>
                    {b.mandatory && <Badge variant="danger">{t("officer.xaiMandatory")}</Badge>}
                    {b.clause_ref && <Badge variant="default">{b.clause_ref}</Badge>}
                    {b.weight != null && <span className="text-[11px] text-slate-500">weight {b.weight}</span>}
                  </div>
                  <p className="mt-1.5 text-xs text-slate-600">{b.evidence?.message}</p>
                  {b.statutory_basis && (
                    <p className="mt-1.5 rounded bg-white/80 p-2 text-[11px] italic leading-snug text-slate-600">
                      <span className="font-semibold not-italic">{t("officer.xaiStatutory")}:</span> {b.statutory_basis}
                    </p>
                  )}
                  <p className="mt-1 text-[11px] text-slate-500">{b.impact}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Remediation */}
        {detail.remediation.length > 0 && (
          <div>
            <div className="label mb-1">{t("officer.xaiRemediation")}</div>
            <ol className="list-decimal space-y-1 pl-5 text-sm text-slate-700">
              {detail.remediation.map((r, i) => (
                <li key={i}>{r.action}</li>
              ))}
            </ol>
          </div>
        )}

        {/* Citations */}
        {detail.citations.length > 0 && (
          <div>
            <div className="label mb-1 flex items-center gap-1.5">
              <ScrollText size={13} className="text-navy-700" /> {t("officer.xaiCitations")}
            </div>
            <ul className="space-y-0.5 text-xs text-slate-600">
              {detail.citations.map((c) => (
                <li key={c.id}>
                  • {c.title}
                  {c.refs.length > 0 && <span className="text-slate-400"> [{c.refs.join(", ")}]</span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Governance / RAG transparency */}
        <div className="rounded-md border border-navy-100 bg-navy-50/40 p-3">
          <div className="label mb-1 flex items-center gap-1.5">
            <Route size={13} className="text-navy-700" /> {t("officer.xaiGovernance")}
          </div>
          <dl className="grid gap-x-6 gap-y-1 text-xs text-slate-600 sm:grid-cols-2">
            <div><dt className="inline font-semibold text-slate-700">{t("officer.xaiDecidedBy")}: </dt><dd className="inline">{gov.decided_by}</dd></div>
            <div><dt className="inline font-semibold text-slate-700">{t("officer.xaiAiRole")}: </dt><dd className="inline">{gov.ai_role}</dd></div>
            <div className="sm:col-span-2"><dt className="inline font-semibold text-slate-700">{t("officer.xaiRetrieval")}: </dt><dd className="inline">{gov.retrieval}</dd></div>
            <div className="sm:col-span-2"><dt className="inline font-semibold text-slate-700">{t("officer.xaiFinalAuthority")}: </dt><dd className="inline">{gov.final_authority}</dd></div>
          </dl>
          {gov.retrieved_passages && gov.retrieved_passages.length > 0 && (
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] font-medium text-navy-700">
                {t("officer.xaiRetrievedPassages")} ({gov.retrieved_passages.length})
              </summary>
              <ul className="mt-1 space-y-1 text-[11px] text-slate-600">
                {gov.retrieved_passages.map((p) => (
                  <li key={p.id}>
                    {p.title} <span className="text-slate-400">score {p.score.toFixed(1)}</span>
                    {p.matched_on.length > 0 && <span className="text-slate-400"> · matched: {p.matched_on.join(", ")}</span>}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export function ReportTab({ rep, onDownload }: { rep: VerificationReport | null; onDownload?: () => void }) {
  const t = useTranslations();
  if (!rep) {
    return (
      <Card className="p-10 text-center">
        <Search className="mx-auto h-9 w-9 text-slate-300" />
        <p className="mt-2 text-sm text-slate-500">
          {t("officer.noReportYet")}
        </p>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      {rep.stale_due_to_corrigendum && (
        <Alert variant="warning" title={t("officer.staleCorrigendumTitle")}>
          <p>{t("officer.staleCorrigendumDesc")}</p>
          {rep.latest_corrigendum && (
            <p className="mt-1 text-xs">
              {rep.latest_corrigendum.seq_no}. {rep.latest_corrigendum.title}
              {rep.latest_corrigendum.published_at ? ` · ${fmtDateTime(rep.latest_corrigendum.published_at)}` : ""}
            </p>
          )}
          <p className="mt-1 text-xs font-medium">{t("officer.rerunToRefresh")}</p>
        </Alert>
      )}

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <div>
            <CardTitle>{t("officer.reportTitle")}</CardTitle>
            <CardDescription>
              Run #{rep.run_no} · {fmtDateTime(rep.generated_at)}
              {rep.rules_version_at_run != null && <> · {t("officer.rulesVersion")}: v{rep.rules_version_at_run}</>}
              {" · "}{rep.model_identifier ?? "AI model"}
            </CardDescription>
            {rep.coverage_pct != null && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="rounded-md border border-navy-200 bg-navy-50 px-2 py-0.5 text-xs font-semibold text-navy-800">
                  {t("officer.coverage")}: {Math.round(rep.coverage_pct)}%
                </span>
                {rep.strength_distribution && Object.entries(rep.strength_distribution).length > 0 && (
                  <span className="flex flex-wrap gap-1">
                    {Object.entries(rep.strength_distribution)
                      .sort((a, b) => b[0].localeCompare(a[0]))
                      .map(([s, n]) => (
                        <span key={s} className={`rounded-md border px-1.5 py-0.5 text-[11px] font-bold ${STRENGTH_META[s]?.cls ?? "bg-slate-100 text-slate-600 border-slate-300"}`}
                              title={STRENGTH_META[s]?.label}>
                          {s}×{n}
                        </span>
                      ))}
                  </span>
                )}
              </div>
            )}
          </div>
          {onDownload && (
            <Button size="sm" variant="outline" onClick={onDownload}>
              <Download size={13} /> {t("officer.reportPdf")}
            </Button>
          )}
        </CardHeader>
        <CardContent className="flex flex-col items-center gap-6 sm:flex-row">
          <ScoreRing score={rep.compliance_score} risk={rep.risk_level} label="score" />
          <div className="flex-1 space-y-2">
            <div className="flex flex-wrap gap-2">
              <EligibilityBadge value={rep.eligibility_result} />
              <RiskBadge risk={rep.risk_level} />
            </div>
            {rep.recommendation && (
              <Alert variant="info" title={t("officer.recommendation")}>{rep.recommendation}</Alert>
            )}
            {rep.system_boundary && (
              <p className="text-[11px] leading-snug text-slate-500">{rep.system_boundary}</p>
            )}
            {rep.ai_explanation && (
              <div>
                <div className="label mb-1">{t("officer.aiExplanation")}</div>
                <p className="whitespace-pre-line rounded-md bg-slate-50 p-3 text-sm leading-relaxed text-slate-700">{rep.ai_explanation}</p>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {rep.explanation_detail && <XaiSection detail={rep.explanation_detail} t={t} />}

      <Card>
        <CardHeader>
          <CardTitle>{t("officer.complianceMatrix")}</CardTitle>
          <CardDescription>{t("officer.complianceMatrixDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <th className="py-2 pr-3">{t("officer.clauseCol")}</th>
                <th className="py-2 pr-3">Rule</th>
                <th className="py-2 pr-3">Source</th>
                <th className="py-2 pr-3">{t("officer.strength")}</th>
                <th className="py-2 pr-3">{t("common.status")}</th>
                <th className="py-2">Detail</th>
              </tr>
            </thead>
            <tbody>
              {(["STATUTORY", "SECTOR_TECHNICAL"] as const).map((layer) => {
                const rows = rep.rule_results.filter((r) => (r.compliance_layer ?? "STATUTORY") === layer);
                if (rows.length === 0) return null;
                return [
                  <tr key={layer} className="bg-slate-50/80">
                    <td colSpan={6} className="px-1 py-1.5 text-xs font-semibold uppercase tracking-wide text-navy-800">
                      {layer === "STATUTORY" ? t("bidder.layerStatutory") : t("bidder.layerSector")}
                    </td>
                  </tr>,
                  ...rows.map((r) => (
                    <tr key={r.rule_id} className="border-t border-slate-100">
                      <td className="py-2.5 pr-3 text-xs text-slate-500">{r.clause_ref ?? "-"}</td>
                      <td className="py-2.5 pr-3">
                        <div className="font-medium text-slate-800">{r.label}</div>
                        <div className="text-[11px] text-slate-500">
                          {r.mandatory && "Mandatory · "}{r.hard_disqualification && "Hard disqualification · "}{r.weight ? `weight ${r.weight}` : ""}
                        </div>
                      </td>
                      <td className="py-2.5 pr-3 text-xs text-slate-600">{r.source}</td>
                      <td className="py-2.5 pr-3">
                        {r.evidence_strength ? (
                          <span className={`inline-flex rounded-md border px-1.5 py-0.5 text-[11px] font-bold ${STRENGTH_META[r.evidence_strength]?.cls ?? "bg-slate-100 text-slate-600 border-slate-300"}`}
                                title={STRENGTH_META[r.evidence_strength]?.label}>
                            {r.evidence_strength}
                          </span>
                        ) : "-"}
                      </td>
                      <td className="py-2.5 pr-3"><ResultBadge result={r.result} /></td>
                      <td className="py-2.5 text-xs text-slate-600">{r.message}</td>
                    </tr>
                  )),
                ];
              })}
            </tbody>
          </table>
        </CardContent>
      </Card>

      {rep.cross_verification.length > 0 && (
        <Card>
          <CardHeader><CardTitle>{t("officer.comparisonTitle")}</CardTitle></CardHeader>
          <CardContent>
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <th className="py-2 pr-3">Field</th>
                  <th className="py-2 pr-3">Bidder</th>
                  <th className="py-2 pr-3">Government source</th>
                  <th className="py-2 pr-3">Source</th>
                  <th className="py-2">{t("common.status")}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rep.cross_verification.map((c, i) => (
                  <tr key={i}>
                    <td className="py-2.5 pr-3 font-medium text-slate-800">{c.field}</td>
                    <td className="py-2.5 pr-3 text-xs">{c.bidder_value ?? "-"}</td>
                    <td className="py-2.5 pr-3 text-xs">{c.source_value ?? "-"}</td>
                    <td className="py-2.5 pr-3 text-xs text-slate-500">{c.source}</td>
                    <td className="py-2.5">
                      <Badge variant={c.result === "MATCH" ? "success" : c.result === "MISMATCH" ? "danger" : "warning"}>
                        {c.result.replace(/_/g, " ")}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {(rep.missing_documents.length > 0 || rep.expired_documents.length > 0) && (
        <Card>
          <CardHeader><CardTitle>{t("officer.documentGaps")}</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {rep.missing_documents.length > 0 && (
              <div>
                <div className="label">{t("officer.missing")}</div>
                <ul className="list-disc pl-5 text-sm text-slate-700">{rep.missing_documents.map((m, i) => <li key={i}>{m}</li>)}</ul>
              </div>
            )}
            {rep.expired_documents.length > 0 && (
              <div>
                <div className="label">{t("officer.expired")}</div>
                <ul className="list-disc pl-5 text-sm text-slate-700">
                  {rep.expired_documents.map((m, i) => <li key={i}>{m.name} ({t("officer.expiredOn")} {fmtDate(m.expiry_date)})</li>)}
                </ul>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {rep.blacklist_findings.length > 0 && (
        <Card>
          <CardHeader><CardTitle>{t("officer.blacklistResult")}</CardTitle></CardHeader>
          <CardContent>
            {rep.blacklist_findings.map((b, i) => (
              <Alert key={i} variant="critical" className="mb-2">{JSON.stringify(b)}</Alert>
            ))}
          </CardContent>
        </Card>
      )}

      {rep.source_results && Object.keys(rep.source_results).length > 0 && (
        <Card>
          <CardHeader><CardTitle>{t("officer.sourceResults")}</CardTitle></CardHeader>
          <CardContent>
            <div className="grid gap-2 sm:grid-cols-2">
              {Object.entries(rep.source_results).map(([k, v]) => (
                <div key={k} className="flex items-center justify-between rounded-md border border-slate-200 px-3 py-2">
                  <span className="text-sm font-medium text-slate-700">{k}</span>
                  <Badge variant={v.status === "VERIFIED" || v.status === "MATCH" ? "success" : v.status === "UNAVAILABLE" || v.status === "UNABLE_TO_VERIFY" ? "warning" : "slate"}>
                    {v.status}
                  </Badge>
                </div>
              ))}
            </div>
            <p className="mt-2 text-[11px] text-slate-500">{t("officer.sourceFailNote")}</p>
          </CardContent>
        </Card>
      )}

      {rep.authenticity.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t("officer.authenticity")}</CardTitle>
            <CardDescription>{t("officer.authenticityHint")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {rep.authenticity.map((a) => (
              <div key={a.document_id} className="rounded-md border border-slate-200 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-slate-800">{a.document_name}</span>
                  <Badge variant={authenticityVariant(a.status)}>{a.status.replace(/_/g, " ")}</Badge>
                </div>
                {a.reasons.length > 0 && (
                  <ul className="list-disc pl-5 text-xs text-slate-600">
                    {a.reasons.map((r, i) => <li key={i}>{r}</li>)}
                  </ul>
                )}
                {a.tampering_indicators && a.tampering_indicators.length > 0 && (
                  <p className="mt-1 text-xs text-red-700">{t("officer.indicators")}: {a.tampering_indicators.join(", ")}</p>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
