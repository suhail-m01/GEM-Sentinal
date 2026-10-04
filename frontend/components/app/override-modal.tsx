"use client";

import React, { useState } from "react";
import { useTranslations } from "next-intl";
import { AlertTriangle, Upload } from "lucide-react";
import { api, upload } from "@/lib/api";
import type { OverrideChallenge } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/fields";
import { RiskBadge, EligibilityBadge } from "@/components/app/widgets";

export function OverrideModal({
  prefix,
  applicationId,
  challenge,
  onClose,
  onDone,
}: {
  prefix: "/officer" | "/l2";
  applicationId: string;
  challenge: OverrideChallenge | null;
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const t = useTranslations();
  const [category, setCategory] = useState("");
  const [justification, setJustification] = useState("");
  const [password, setPassword] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [evidence, setEvidence] = useState<{ name: string; file_path: string; sha256: string }[]>([]);

  if (!challenge) return null;
  const minLen = category === "OTHER" ? challenge.min_other_justification_length : challenge.min_justification_length;
  const f = challenge.finding;

  const uploadEvidence = async () => {
    if (!file) return;
    setUploading(true);
    setErr("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await upload<{ name: string; file_path: string; sha256: string }>(
        `${prefix}/applications/${applicationId}/override-evidence-upload`,
        fd
      );
      setEvidence([...evidence, r]);
      setFile(null);
    } catch (e2: any) {
      setErr(e2?.detail ?? String(e2));
    } finally {
      setUploading(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      // 1. password re-authentication -> short-lived reauth token
      const ra = await api<{ reauth_token: string }>(`/auth/reauth`, {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      // 2. record the accountable override with the reauth token
      const r = await api<{ message: string }>(`${prefix}/applications/${applicationId}/override`, {
        method: "POST",
        body: JSON.stringify({
          challenge: challenge.challenge,
          reason_category: category,
          justification,
          reauth_token: ra.reauth_token,
          evidence,
        }),
      });
      onDone(r.message);
    } catch (e2: any) {
      const det = e2?.detail;
      setErr(typeof det === "string" ? det : det?.message ?? JSON.stringify(det) ?? String(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={!!challenge} onClose={onClose} title={t("officer.overrideTitle")} wide>
      <form onSubmit={submit} className="space-y-5">
        <Alert variant="critical" title={t("officer.overrideTitle")}>
          {t("officer.overrideWarning")}
        </Alert>

        {/* System finding */}
        <div>
          <div className="label mb-2">{t("officer.systemFinding")}</div>
          <div className="grid gap-3 rounded-md border border-slate-200 bg-slate-50 p-4 sm:grid-cols-4">
            <div>
              <div className="label">{t("common.risk")}</div>
              <RiskBadge risk={f.risk_level} />
            </div>
            <div>
              <div className="label">{t("common.eligibility")}</div>
              <EligibilityBadge value={f.eligibility_result} />
            </div>
            <div>
              <div className="label">{t("common.score")}</div>
              <div className="text-lg font-bold text-navy-900">{f.compliance_score ?? "-"}</div>
            </div>
            <div>
              <div className="label">Blacklisted</div>
              <Badge variant={f.blacklisted ? "critical" : "success"}>{f.blacklisted ? "YES" : "NO"}</Badge>
            </div>
          </div>
          {f.blacklist_findings.length > 0 && (
            <div className="mt-2">
              {f.blacklist_findings.map((b, i) => (
                <Alert key={i} variant="danger" className="mt-1">{JSON.stringify(b)}</Alert>
              ))}
            </div>
          )}
          {f.failed_rules.length > 0 && (
            <div className="mt-2">
              <div className="label">{t("officer.evidenceFor")} - failed rules</div>
              <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
                {f.failed_rules.map((r, i) => (
                  <li key={i}>
                    <b>{r.label}</b>: {r.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {f.unverified_mandatory.length > 0 && (
            <div className="mt-2">
              <div className="label">Unverified mandatory requirements</div>
              <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
                {f.unverified_mandatory.map((r, i) => (
                  <li key={i}>
                    <b>{r.label}</b>: {r.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {f.mismatches.length > 0 && (
            <div className="mt-2">
              <div className="label">Cross-verification mismatches</div>
              <ul className="list-disc space-y-1 pl-5 text-sm text-slate-700">
                {f.mismatches.map((m, i) => (
                  <li key={i}>
                    {m.field}: “{m.bidder_value}” vs “{m.source_value}” ({m.source})
                  </li>
                ))}
              </ul>
            </div>
          )}
          {f.ai_explanation && <p className="mt-2 rounded-md bg-slate-50 p-2 text-xs text-slate-600">{f.ai_explanation}</p>}
        </div>

        {/* Accountability form */}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("officer.reasonCategory")} required>
            <Select required value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">-</option>
              {challenge.reason_categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label={t("officer.reauthLabel")} required>
            <Input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
          </Field>
        </div>
        <Field
          label={t("officer.justificationLabel")}
          required
          hint={
            justification.length < minLen
              ? `${justification.length}/${minLen} characters minimum`
              : t("officer.justificationHint", { n: minLen })
          }
        >
          <Textarea
            required
            minLength={minLen}
            rows={4}
            value={justification}
            onChange={(e) => setJustification(e.target.value)}
          />
        </Field>

        <div className="flex flex-wrap items-center gap-3">
          <Input
            type="file"
            accept=".pdf,.jpg,.jpeg,.png"
            className="max-w-xs"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <Button type="button" size="sm" variant="outline" onClick={uploadEvidence} loading={uploading} disabled={!file}>
            <Upload size={13} /> {t("common.upload")}
          </Button>
          {evidence.map((ev, i) => (
            <Badge key={i} variant="success">{ev.name}</Badge>
          ))}
        </div>

        {err && <Alert variant="danger">{err}</Alert>}

        <div className="flex items-center justify-between border-t border-slate-200 pt-4">
          <p className="flex items-center gap-1.5 text-xs text-slate-500">
            <AlertTriangle size={13} /> {t("common.audit")} - this action is hash-chained and immutable.
          </p>
          <div className="flex gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>{t("common.cancel")}</Button>
            <Button type="submit" variant="critical" loading={busy} disabled={justification.length < minLen || !password || !category}>
              {t("officer.confirmOverride")}
            </Button>
          </div>
        </div>
      </form>
    </Dialog>
  );
}
