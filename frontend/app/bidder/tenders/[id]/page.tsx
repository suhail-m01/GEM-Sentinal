"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { api } from "@/lib/api";
import type { AuthorityBand, Corrigendum, ProcuringOrg, RuleCheck, SourceResult } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge, eligibilityVariant } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Textarea, Select } from "@/components/ui/fields";
import { Loading, EligibilityBadge, ScoreRing, ResultBadge } from "@/components/app/widgets";
import { fmtDate, fmtDateTime, formatINR, formatPaise, parsePaise } from "@/lib/utils";

interface TenderDetail {
  id: string;
  tender_no: string;
  title: string;
  description: string | null;
  department: string;
  organization: string;
  district: string;
  state: string;
  category: string | null;
  product_service_category: string | null;
  procurement_method?: string;
  procuring_organization?: ProcuringOrg | null;
  authority_chain?: AuthorityBand[] | null;
  corrigenda?: Corrigendum[];
  value_min: number | null;
  value_max: number | null;
  financial_bid_required?: boolean;
  close_date: string;
  open_date: string | null;
  msme_preference: boolean;
  make_in_india_required: boolean;
  startup_eligible: boolean;
  required_turnover: number | null;
  min_local_content: number | null;
  required_document_types: string[];
  notes: string | null;
  payment_requirements?: {
    application_fee: { required: boolean; amount: number; paid: boolean; refundable: boolean };
    emd: { required: boolean; amount: number; paid: boolean; exempt: boolean; exemption_allowed: boolean; percent: number | null };
    checkout: { payment_type: string; amount: number; paid: boolean; includes_application_fee: boolean; includes_emd: boolean };
    gateway_configured: boolean;
    all_satisfied: boolean;
  };
  existing_application: {
    application_no: string;
    status: string;
    compliance_score: number | null;
    submitted_at: string | null;
  } | null;
  eligibility: {
    result: string;
    label: string;
    apply_allowed: boolean;
    apply_blocked_reasons: string[];
    compliance_score: number;
    risk_level: string;
    risk_label: string;
    checklist: RuleCheck[];
    missing_documents: string[];
    mismatches: Array<{ field: string; bidder_value: string; source_value: string; source: string }>;
    blacklist_findings: Record<string, unknown>[];
    source_results: Record<string, SourceResult>;
  };
}

export default function TenderDetailPage() {
  const t = useTranslations();
  const params = useParams<{ id: string }>();
  const [d, setD] = useState<TenderDetail | null>(null);
  const [err, setErr] = useState("");
  const [applyOpen, setApplyOpen] = useState(false);
  const [declaration, setDeclaration] = useState("");
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [remarks, setRemarks] = useState("");
  const [submitMsg, setSubmitMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [blocked, setBlocked] = useState<string[]>([]);
  const [consentGranted, setConsentGranted] = useState(false);
  // Financial envelope (quotation). Amounts are kept as the user's decimal
  // strings and sent verbatim; the server re-validates with Decimal.
  const [baseAmount, setBaseAmount] = useState("");
  const [taxAmount, setTaxAmount] = useState("");
  const [otherCharges, setOtherCharges] = useState("");
  const [priceValidDays, setPriceValidDays] = useState("90");
  const [commercialDeclaration, setCommercialDeclaration] = useState("");
  const [paymentBusy, setPaymentBusy] = useState<string | null>(null);
  const [paymentMsg, setPaymentMsg] = useState<string | null>(null);

  const draftKey = `gs_apply_draft_${params.id}`;
  // Stable per-attempt key: a retry after a network blip must not seal a
  // second envelope. Rotated only after a successful submission.
  const idemKeyRef = React.useRef<string>("");
  if (!idemKeyRef.current) {
    idemKeyRef.current =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  const load = () => {
    api<TenderDetail>(`/bidder/tenders/${params.id}`)
      .then(setD)
      .catch((e) => setErr(e?.detail ?? String(e)));
  };

  useEffect(() => {
    load();
    // If consent is actually granted (Consent page), pre-tick the confirmation
    // checkbox - it declares "I have granted consent", which is then true.
    api<Array<{ source: string; status: string }>>(`/bidder/consent`)
      .then((rows) => {
        if (rows.some((r) => r.status === "GRANTED")) {
          setConsentGranted(true);
          setConsentConfirmed(true);
        }
      })
      .catch(() => {});
    // Restore a saved application draft (e.g. after the consent round-trip)
    // and re-open the apply dialog when returning via ?resume=1
    try {
      const q = new URLSearchParams(window.location.search);
      const raw = sessionStorage.getItem(draftKey);
      if (raw) {
        const draft = JSON.parse(raw);
        if (draft.declaration) setDeclaration(draft.declaration);
        if (draft.remarks) setRemarks(draft.remarks);
        if (draft.consentConfirmed) setConsentConfirmed(true);
        if (draft.baseAmount) setBaseAmount(draft.baseAmount);
        if (draft.taxAmount) setTaxAmount(draft.taxAmount);
        if (draft.otherCharges) setOtherCharges(draft.otherCharges);
        if (draft.priceValidDays) setPriceValidDays(draft.priceValidDays);
        if (draft.commercialDeclaration) setCommercialDeclaration(draft.commercialDeclaration);
      }
      if (q.get("resume") === "1") setApplyOpen(true);
    } catch {
      /* ignore storage errors */
    }
  }, [params.id, draftKey]);

  // Keep the draft alive while the user fills the form (or steps away to consent)
  useEffect(() => {
    try {
      sessionStorage.setItem(
        draftKey,
        JSON.stringify({
          declaration, remarks, consentConfirmed,
          baseAmount, taxAmount, otherCharges, priceValidDays, commercialDeclaration,
        }),
      );
    } catch {
      /* ignore storage errors */
    }
  }, [declaration, remarks, consentConfirmed, baseAmount, taxAmount, otherCharges,
      priceValidDays, commercialDeclaration, draftKey]);

  // Quote validation mirrors the server rules (§9) so the bidder sees the
  // problem before submitting; the server stays authoritative.
  const financialRequired = d?.financial_bid_required !== false;
  const basePaise = parsePaise(baseAmount);
  const taxPaise = taxAmount.trim() === "" ? 0n : parsePaise(taxAmount);
  const otherPaise = otherCharges.trim() === "" ? 0n : parsePaise(otherCharges);
  const validDaysNum = Number(priceValidDays);
  const totalPaise =
    basePaise !== null && taxPaise !== null && otherPaise !== null
      ? basePaise + taxPaise + otherPaise
      : null;
  const quoteError: string | null = !financialRequired
    ? null
    : basePaise === null
      ? baseAmount.trim() === ""
        ? null
        : "Enter the base amount in rupees, up to 2 decimals."
      : basePaise <= 0n
        ? "Base quoted amount must be greater than zero."
        : taxPaise === null
          ? "Tax amount is not a valid amount."
          : otherPaise === null
            ? "Other charges is not a valid amount."
            : !Number.isInteger(validDaysNum) || validDaysNum < 1 || validDaysNum > 365
              ? "Price validity must be between 1 and 365 days."
              : null;
  const quoteReady =
    !financialRequired ||
    (basePaise !== null && basePaise > 0n && quoteError === null && commercialDeclaration.trim().length >= 20);

  // Payment is intentionally the final step before application submission.
  // The bidder must finish the declaration, consent and financial quote first.
  const readyForPayment = declaration.trim().length >= 40 && consentConfirmed && quoteReady;

  const ensureRazorpay = async () => {
    if ((window as any).Razorpay) return true;
    return await new Promise<boolean>((resolve) => {
      const script = document.createElement("script");
      script.src = "https://checkout.razorpay.com/v1/checkout.js";
      script.onload = () => resolve(true);
      script.onerror = () => resolve(false);
      document.body.appendChild(script);
    });
  };

  const payTenderCharge = async () => {
    setPaymentMsg(null);
    if (!readyForPayment) {
      setPaymentMsg("Complete the declaration, consent and financial quotation before payment.");
      return;
    }
    setPaymentBusy("AUTO");
    try {
      const loaded = await ensureRazorpay();
      if (!loaded) throw new Error("Could not load Razorpay Checkout.");
      const order = await api<any>(`/bidder/tenders/${params.id}/payments/order`, {
        method: "POST",
        body: JSON.stringify({ payment_type: "AUTO" }),
      });
      if (order.already_paid) { load(); return; }
      await new Promise<void>((resolve, reject) => {
        const rz = new (window as any).Razorpay({
          key: order.key_id,
          amount: order.amount,
          currency: order.currency,
          name: order.name,
          description: order.description,
          order_id: order.order_id,
          theme: { color: "#1e3a5f" },
          handler: async (response: any) => {
            try {
              await api(`/bidder/tenders/${params.id}/payments/verify`, {
                method: "POST",
                body: JSON.stringify({
                  razorpay_order_id: response.razorpay_order_id,
                  razorpay_payment_id: response.razorpay_payment_id,
                  razorpay_signature: response.razorpay_signature,
                }),
              });
              resolve();
            } catch (e) { reject(e); }
          },
          modal: { ondismiss: () => reject(new Error("Payment cancelled")) },
        });
        rz.open();
      });
      setPaymentMsg("Tender payment verified successfully.");
      load();
    } catch (e: any) {
      setPaymentMsg(typeof e?.detail === "string" ? e.detail : e?.detail?.message ?? e?.message ?? String(e));
    } finally {
      setPaymentBusy(null);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setSubmitMsg(null);
    try {
      const r = await api<{ application_no: string }>(`/bidder/applications?tender_id=${params.id}`, {
        method: "POST",
        body: JSON.stringify({
          declaration_text: declaration,
          consent_confirmed: consentConfirmed,
          remarks: remarks || undefined,
          financial_bid: financialRequired
            ? {
                base_amount: baseAmount.trim(),
                tax_amount: taxAmount.trim() || "0",
                other_charges: otherCharges.trim() || "0",
                currency: "INR",
                price_valid_days: Number(priceValidDays),
                commercial_declaration: commercialDeclaration.trim(),
                // Guards against a double-submit sealing two envelopes.
                idempotency_key: idemKeyRef.current,
              }
            : undefined,
        }),
      });
      try {
        sessionStorage.removeItem(draftKey);
      } catch {
        /* ignore */
      }
      setApplyOpen(false);
      // New envelope next time; clear the quote so it is never silently reused.
      idemKeyRef.current = "";
      setBaseAmount(""); setTaxAmount(""); setOtherCharges("");
      setCommercialDeclaration(""); setPriceValidDays("90");
      // Show the Application ID confirmation inline (trackable end-to-end).
      setSubmitMsg({ ok: true, text: t("bidder.applySubmittedId", { id: r.application_no }) });
      load();
    } catch (e2: any) {
      const det = e2?.detail;
      if (e2?.status === 409 && typeof det === "object") {
        setBlocked((det as any).reasons ?? []);
        setApplyOpen(false);
      }
      setSubmitMsg({ ok: false, text: typeof det === "string" ? det : det?.message ?? String(e2) });
    } finally {
      setSubmitting(false);
    }
  };

  if (err) return <Alert variant="danger">{err}</Alert>;
  if (!d) return <Loading />;
  const el = d.eligibility;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/bidder/tenders" className="text-sm text-navy-700 hover:underline">← {t("nav.tenders")}</Link>
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold text-navy-900">{d.title}</h1>
            <p className="text-sm text-slate-500">
              {d.tender_no} · {d.organization}, {d.department} · {d.district}, {d.state}
            </p>
          </div>
          <Button disabled={!el.apply_allowed} onClick={() => { setSubmitMsg(null); setApplyOpen(true); }} size="lg">
            {t("common.apply")}
          </Button>
        </div>
      </div>

      {d.existing_application && (
        <Alert variant="info" title={t("bidder.alreadyAppliedTitle")}>
          <span>
            {d.existing_application.application_no} - {d.existing_application.status} ·{" "}
            <Link href="/bidder/applications" className="font-medium underline">
              {t("nav.applications")} →
            </Link>
          </span>
        </Alert>
      )}

      {blocked.length > 0 && (
        <Alert variant="critical" title={t("bidder.applyBlockTitle")}>
          <p className="mb-1">{t("bidder.applyBlockDesc")}</p>
          <ul className="list-disc pl-5">
            {blocked.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ul>
        </Alert>
      )}

      {d.procuring_organization && (
        <Card>
          <CardHeader>
            <CardTitle>{t("bidder.procuringOrg")}</CardTitle>
            <CardDescription>{t("bidder.procuringOrgDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 text-sm sm:grid-cols-3">
            <div>
              <span className="label">{t("bidder.orgName")}</span>
              <div className="font-medium text-slate-900">{d.procuring_organization.name}</div>
              <div className="text-xs text-slate-500">
                {d.procuring_organization.org_type}
                {d.procuring_organization.parent_ministry ? ` · ${d.procuring_organization.parent_ministry}` : ""}
              </div>
            </div>
            <div>
              <span className="label">{t("bidder.procurementDivision")}</span>
              <div>{d.procuring_organization.procurement_division ?? "-"}</div>
              <div className="text-xs text-slate-500">
                {t("bidder.procurementMethod")}: {d.procurement_method ?? "GEM_BID"}
              </div>
            </div>
            <div>
              <span className="label">{t("bidder.awardAuthority")}</span>
              {(d.authority_chain?.length ? d.authority_chain : []).map((b, i) => (
                <div key={i} className="text-xs leading-relaxed">
                  <span className="font-medium text-slate-700">{b.band}:</span>{" "}
                  {b.review_role_title} → {b.approval_role_title}
                  {b.committee_required && (
                    <Badge variant="info" className="ml-1">{t("bidder.tenderCommittee")}</Badge>
                  )}
                </div>
              ))}
              {!d.authority_chain?.length && <div className="text-xs text-slate-500">-</div>}
            </div>
          </CardContent>
        </Card>
      )}

      {d.corrigenda && d.corrigenda.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t("bidder.corrigendaTitle")}</CardTitle>
            <CardDescription>{t("bidder.corrigendaDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {d.corrigenda.map((c) => (
              <div key={c.seq_no} className="rounded-md border border-amber-200 bg-amber-50/60 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-amber-900">
                    {c.seq_no}. {c.title}
                  </span>
                  <span className="text-xs text-slate-500">{c.published_at ? fmtDate(c.published_at) : ""}</span>
                </div>
                <p className="mt-1 text-xs text-slate-700">{c.summary}</p>
                {c.changes && c.changes.length > 0 && (
                  <table className="mt-2 w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-amber-200 text-[11px] uppercase tracking-wide text-amber-800">
                        <th className="py-1 pr-3">{t("bidder.clause")}</th>
                        <th className="py-1 pr-3">{t("bidder.was")}</th>
                        <th className="py-1">{t("bidder.now")}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-amber-100">
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
                  <p className="mt-2 text-xs font-medium text-amber-800">
                    {t("bidder.deadlineExtended")}: {fmtDate(c.deadline_extended_to)}
                  </p>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>{t("common.details")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="whitespace-pre-line text-sm text-slate-700">{d.description || "-"}</p>
            <div className="grid gap-3 text-sm sm:grid-cols-2">
              <div><span className="label">{t("common.value")}</span>
                <div className="font-semibold">{d.value_min && d.value_max ? `${formatINR(d.value_min)} – ${formatINR(d.value_max)}` : formatINR(d.value_max ?? d.value_min ?? 0)}</div>
              </div>
              <div><span className="label">{t("common.closeDate")}</span><div>{fmtDate(d.close_date)}{d.open_date ? ` (opens ${fmtDate(d.open_date)})` : ""}</div></div>
              <div><span className="label">{t("common.category")}</span><div>{d.category} {d.product_service_category ? `· ${d.product_service_category}` : ""}</div></div>
              <div><span className="label">Min turnover</span><div>{d.required_turnover ? formatINR(d.required_turnover) : "-"}</div></div>
              <div><span className="label">Min local content</span><div>{d.min_local_content ? `${d.min_local_content}%` : "-"}</div></div>
              <div>
                <span className="label">Required documents</span>
                <div className="mt-1 flex flex-wrap gap-1">
                  {(d.required_document_types?.length ? d.required_document_types : ["-"]).map((x) => (
                    <Badge key={x} variant="slate">{x.replace(/_/g, " ").toLowerCase()}</Badge>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {d.msme_preference && <Badge variant="info">MSME Preference</Badge>}
              {d.make_in_india_required && <Badge variant="info">Make in India Required</Badge>}
              {d.startup_eligible && <Badge variant="info">Startup Eligible</Badge>}
            </div>
            {d.notes && <p className="rounded-md bg-slate-50 p-3 text-xs text-slate-600">{d.notes}</p>}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("bidder.precheckTitle")}</CardTitle>
            <CardDescription>Live pre-check from your verified vault</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-center">
              <ScoreRing score={el.compliance_score} risk={el.risk_level} />
            </div>
            <div className="flex justify-center">
              <EligibilityBadge value={el.result} />
            </div>
            {el.apply_allowed ? (
              <Alert variant="success">{t("bidder.applyAllowDesc")}</Alert>
            ) : (
              <Alert variant="critical">
                {t("bidder.applyBlockDesc")}
                <ul className="mt-1 list-disc pl-4">
                  {el.apply_blocked_reasons.map((b, i) => (
                    <li key={i}>{b}</li>
                  ))}
                </ul>
              </Alert>
            )}
            <div>
              <div className="label mb-1">Source status</div>
              <div className="space-y-1">
                {Object.entries(el.source_results).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between text-xs">
                    <span className="text-slate-600">{k}</span>
                    <Badge variant={v.status === "VERIFIED" ? "success" : v.status === "UNAVAILABLE" || v.status === "UNABLE_TO_VERIFY" ? "warning" : v.status === "MATCH" ? "success" : "slate"}>
                      {v.status}
                    </Badge>
                  </div>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("bidder.eligibilityChecklist")}</CardTitle>
          <CardDescription>{t("bidder.twoLayerDesc")}</CardDescription>
        </CardHeader>
        <CardContent>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                <th className="py-2 pr-3">{t("common.category")}</th>
                <th className="py-2 pr-3">{t("bidder.clause")}</th>
                <th className="py-2 pr-3">{t("common.status")}</th>
                <th className="py-2">{t("common.details")}</th>
              </tr>
            </thead>
            <tbody>
              {(["STATUTORY", "SECTOR_TECHNICAL"] as const).map((layer) => {
                const rows = el.checklist.filter((r) => (r.compliance_layer ?? "STATUTORY") === layer);
                if (rows.length === 0) return null;
                return [
                  <tr key={layer} className="bg-slate-50/80">
                    <td colSpan={4} className="px-1 py-1.5 text-xs font-semibold uppercase tracking-wide text-navy-800">
                      {layer === "STATUTORY" ? t("bidder.layerStatutory") : t("bidder.layerSector")}
                    </td>
                  </tr>,
                  ...rows.map((r) => (
                    <tr key={r.rule_key} className="border-t border-slate-100">
                      <td className="py-2.5 pr-3">
                        <div className="font-medium text-slate-800">{r.label}</div>
                        <div className="text-xs text-slate-500">{r.source}{r.mandatory ? " · Mandatory" : ""}{r.hard_disqualification ? " · Hard disqualification" : ""}</div>
                      </td>
                      <td className="py-2.5 pr-3 text-xs text-slate-500">{r.clause_ref ?? "-"}</td>
                      <td className="py-2.5 pr-3"><ResultBadge result={r.result} /></td>
                      <td className="py-2.5 text-xs text-slate-600">{r.message}</td>
                    </tr>
                  )),
                ];
              })}
            </tbody>
          </table>
          {el.missing_documents.length > 0 && (
            <Alert variant="warning" className="mt-4" title="Missing documents">
              <ul className="list-disc pl-5">{el.missing_documents.map((m, i) => <li key={i}>{m}</li>)}</ul>
            </Alert>
          )}
          {el.mismatches.length > 0 && (
            <Alert variant="danger" className="mt-4" title="Mismatches detected">
              <ul className="list-disc pl-5">
                {el.mismatches.map((m, i) => (
                  <li key={i}>{m.field}: you have “{m.bidder_value}” vs source “{m.source_value}” ({m.source})</li>
                ))}
              </ul>
            </Alert>
          )}
          {el.blacklist_findings.length > 0 && (
            <Alert variant="critical" className="mt-4" title="Blacklist / debarment findings">
              <ul className="list-disc pl-5">
                {el.blacklist_findings.map((b, i) => (
                  <li key={i}>{JSON.stringify(b)}</li>
                ))}
              </ul>
            </Alert>
          )}
        </CardContent>
      </Card>

      <Dialog open={applyOpen} onClose={() => setApplyOpen(false)} title={`${t("common.apply")} - ${d.tender_no}`}>
        <form onSubmit={submit} className="space-y-4">
          <Alert variant="info">
            {t("bidder.applySnapshot")}{" "}
            <span className="text-xs text-slate-600">({t("bidder.snapshotFrozen")})</span>
          </Alert>
          {submitMsg && <Alert variant={submitMsg.ok ? "success" : "danger"}>{submitMsg.text}</Alert>}
          <Field label={t("bidder.declarationMin")} required hint={declaration.length < 40 ? `${declaration.length}/40 characters` : `${declaration.length} characters`}>
            <Textarea required minLength={40} value={declaration} onChange={(e) => setDeclaration(e.target.value)} placeholder={t("bidder.declarationLabel")} />
          </Field>
          <Field label="Remarks (optional)">
            <Textarea value={remarks} onChange={(e) => setRemarks(e.target.value)} />
          </Field>

          {financialRequired && (
            <div className="rounded-md border border-navy-200 bg-navy-50/60 p-3">
              <div className="flex items-center justify-between">
                <div className="text-sm font-semibold text-navy-900">Financial Quotation</div>
                <span className="rounded border border-navy-300 bg-white px-2 py-0.5 text-[11px] font-medium text-navy-700">
                  Sealed until bid opening
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-600">
                Your price is sealed on submission and stays unreadable to officers until the authorised
                bid opening. Enter amounts in rupees, up to 2 decimals.
              </p>
              {(d.value_min || d.value_max) && (
                <p className="mt-1 text-xs text-slate-500">
                  Tender estimated value: {formatINR(d.value_max ?? d.value_min)}
                </p>
              )}

              <div className="mt-3 grid gap-3 sm:grid-cols-3">
                <Field label="Base amount (INR)" required>
                  <Input
                    inputMode="decimal"
                    value={baseAmount}
                    onChange={(e) => setBaseAmount(e.target.value)}
                    placeholder="9500000.00"
                    aria-label="Base quoted amount in rupees"
                  />
                </Field>
                <Field label="Tax amount (INR)">
                  <Input
                    inputMode="decimal"
                    value={taxAmount}
                    onChange={(e) => setTaxAmount(e.target.value)}
                    placeholder="0.00"
                  />
                </Field>
                <Field label="Other charges (INR)">
                  <Input
                    inputMode="decimal"
                    value={otherCharges}
                    onChange={(e) => setOtherCharges(e.target.value)}
                    placeholder="0.00"
                  />
                </Field>
              </div>

              <div className="mt-3 flex flex-wrap items-end justify-between gap-3">
                <Field label="Price validity (days)" required>
                  <Input
                    type="number"
                    min={1}
                    max={365}
                    value={priceValidDays}
                    onChange={(e) => setPriceValidDays(e.target.value)}
                    className="w-32"
                  />
                </Field>
                <div className="rounded border border-slate-300 bg-white px-3 py-2 text-right">
                  <div className="text-[11px] uppercase tracking-wide text-slate-500">Total quoted value</div>
                  <div className="text-lg font-bold tabular-nums text-navy-900">
                    {totalPaise !== null && basePaise !== null && basePaise > 0n ? formatPaise(totalPaise) : "-"}
                  </div>
                </div>
              </div>

              <div className="mt-3">
                <Field
                  label="Commercial declaration"
                  required
                  hint={`${commercialDeclaration.trim().length}/20 characters minimum`}
                >
                  <Textarea
                    value={commercialDeclaration}
                    onChange={(e) => setCommercialDeclaration(e.target.value)}
                    placeholder="I confirm the quoted price is firm, inclusive of the stated taxes and charges, and valid for the stated period."
                  />
                </Field>
              </div>

              {quoteError && (
                <p className="mt-2 text-xs font-medium text-red-700">{quoteError}</p>
              )}
            </div>
          )}
          <label className="flex cursor-pointer items-start gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={consentConfirmed} onChange={(e) => setConsentConfirmed(e.target.checked)} />
            <span>
              {t("bidder.consentConfirm")}{" "}
              <Link
                href={`/bidder/consent?from=apply&return_to=${encodeURIComponent(`/bidder/tenders/${params.id}`)}&tender=${encodeURIComponent(d.tender_no)}`}
                className="font-medium text-navy-700 underline"
              >
                {t("nav.consent")} →
              </Link>
              {consentGranted && (
                <span className="ml-2 text-xs font-medium text-emerald-700">✓ {t("bidder.consentVerified")}</span>
              )}
            </span>
          </label>
          <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
            <div className="mb-1 font-medium text-slate-700">{t("bidder.whatYouNeed")}</div>
            <ul className="space-y-1">
              <li>
                <span className={declaration.length >= 40 ? "text-emerald-700" : "text-slate-400"}>
                  {declaration.length >= 40 ? "✓" : "○"}
                </span>{" "}
                {t("bidder.reqDeclaration")}{" "}
                <span className="text-slate-400">({declaration.length}/40)</span>
              </li>
              <li>
                <span className={consentConfirmed ? "text-emerald-700" : "text-slate-400"}>
                  {consentConfirmed ? "✓" : "○"}
                </span>{" "}
                {t("bidder.reqConsent")}
              </li>
              {d.payment_requirements && (
                <li>
                  <span className={d.payment_requirements.all_satisfied ? "text-emerald-700" : "text-slate-400"}>
                    {d.payment_requirements.all_satisfied ? "✓" : "○"}
                  </span>{" "}
                  Final payment before submission
                </li>
              )}
              {financialRequired && (
                <li>
                  <span className={quoteReady ? "text-emerald-700" : "text-slate-400"}>
                    {quoteReady ? "✓" : "○"}
                  </span>{" "}
                  Financial quotation with commercial declaration
                </li>
              )}
            </ul>
          </div>
          {d.payment_requirements && (
            <div className="rounded-md border border-navy-200 bg-navy-50/50 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-navy-900">Final Step — Pay & Submit</div>
                  <p className="mt-1 text-xs text-slate-600">
                    Payment is enabled only after your declaration, consent and quotation are complete.
                  </p>
                </div>
                {d.payment_requirements.checkout.paid && <Badge variant="success">Paid & verified</Badge>}
              </div>

              <div className="mt-3 grid gap-3 sm:grid-cols-3">
                <div className="rounded border border-slate-200 bg-white p-3">
                  <div className="text-xs text-slate-500">Application / Platform Fee</div>
                  <div className="mt-1 font-semibold text-slate-900">{formatINR(d.payment_requirements.application_fee.amount)}</div>
                </div>
                <div className="rounded border border-slate-200 bg-white p-3">
                  <div className="text-xs text-slate-500">EMD / Bid Security</div>
                  <div className="mt-1 font-semibold text-slate-900">
                    {d.payment_requirements.emd.exempt
                      ? "Exempt"
                      : d.payment_requirements.emd.required
                        ? formatINR(d.payment_requirements.emd.amount)
                        : "Not required"}
                  </div>
                  {d.payment_requirements.emd.exempt && <div className="mt-1 text-[11px] text-slate-500">MSME/Udyam exemption</div>}
                </div>
                <div className="rounded border border-navy-300 bg-white p-3">
                  <div className="text-xs text-slate-500">Total payable now</div>
                  <div className="mt-1 text-lg font-bold text-navy-900">{formatINR(d.payment_requirements.checkout.amount)}</div>
                </div>
              </div>

              {paymentMsg && (
                <Alert variant={d.payment_requirements.all_satisfied ? "success" : "info"} className="mt-3">
                  {paymentMsg}
                </Alert>
              )}
              {!d.payment_requirements.gateway_configured && !d.payment_requirements.all_satisfied && (
                <Alert variant="warning" className="mt-3">
                  Razorpay Test Mode keys are not configured on the backend.
                </Alert>
              )}

              {!d.payment_requirements.all_satisfied && (
                <div className="mt-3 flex items-center justify-between gap-3">
                  <p className="text-xs text-slate-600">
                    {readyForPayment
                      ? "Your application is ready. Complete payment to unlock Submit Application."
                      : "Complete the declaration, consent and quotation to unlock payment."}
                  </p>
                  <Button
                    type="button"
                    loading={paymentBusy === "AUTO"}
                    disabled={!readyForPayment || !d.payment_requirements.gateway_configured}
                    onClick={payTenderCharge}
                  >
                    {d.payment_requirements.checkout.includes_emd
                      ? `Pay ${formatINR(d.payment_requirements.checkout.amount)} (₹2,500 + EMD)`
                      : `Pay ${formatINR(d.payment_requirements.checkout.amount)}`}
                  </Button>
                </div>
              )}
            </div>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setApplyOpen(false)}>{t("common.cancel")}</Button>
            <Button
              type="submit"
              loading={submitting}
              disabled={!readyForPayment || (d.payment_requirements ? !d.payment_requirements.all_satisfied : false)}
            >
              {d.payment_requirements && !d.payment_requirements.all_satisfied
                ? "Submit Application (Pay First)"
                : t("bidder.submitApplication")}
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
