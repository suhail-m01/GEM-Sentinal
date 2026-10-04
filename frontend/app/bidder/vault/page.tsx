"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Eye, FileText, RefreshCw, Upload } from "lucide-react";
import { api, upload } from "@/lib/api";
import type { DocumentItem } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge, docStatusVariant } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/fields";
import { Loading, Empty } from "@/components/app/widgets";
import { DocViewer, type DocViewerTarget } from "@/components/app/doc-viewer";
import { fmtDate, fmtDateTime } from "@/lib/utils";

const DOC_TYPES = [
  ["UDYAM_CERT", "Udyam/MSME Certificate"],
  ["GST_CERT", "GST Certificate"],
  ["PAN", "PAN Certificate"],
  ["MCA_DOC", "MCA21 Incorporation Certificate"],
  ["EPFO_DOC", "EPFO Compliance Certificate"],
  ["ESIC_DOC", "ESIC Compliance Certificate"],
  ["STARTUP_CERT", "Startup India Recognition"],
  ["NSIC_CERT", "NSIC Registration"],
  ["BIS_CERT", "BIS Certification"],
  ["OTHER", "Other"],
];

export default function VaultPage() {
  const t = useTranslations();
  const [docs, setDocs] = useState<DocumentItem[] | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [fetching, setFetching] = useState(false);
  const [upOpen, setUpOpen] = useState(false);
  const [upForm, setUpForm] = useState({ doc_type: "OTHER", name: "" });
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<DocumentItem | null>(null);
  const [viewing, setViewing] = useState<DocViewerTarget | null>(null);

  const load = () => api<{ documents: DocumentItem[] }>(`/bidder/documents`).then((d) => setDocs(d.documents)).catch(() => setDocs([]));
  useEffect(() => {
    load();
  }, []);

  const fetchDocs = async () => {
    setFetching(true);
    setMsg(null);
    try {
      const r = await api<{ fetched: Array<{ source: string; status: string; message?: string }> }>(`/bidder/fetch-documents`, { method: "POST" });
      setMsg({ ok: true, text: `Fetched from ${r.fetched.length} source(s): ${r.fetched.map((f) => `${f.source}=${f.status}`).join(", ")}` });
      await load();
    } catch (err: any) {
      setMsg({ ok: false, text: err?.detail ?? String(err) });
    } finally {
      setFetching(false);
    }
  };

  const resync = async (id: string) => {
    setMsg(null);
    try {
      const r = await api<{ status: string; message?: string }>(`/bidder/documents/${id}/resync`, { method: "POST" });
      setMsg({ ok: r.status === "VERIFIED", text: `Resync: ${r.status} ${r.message ?? ""}` });
      await load();
    } catch (err: any) {
      setMsg({ ok: false, text: err?.detail ?? String(err) });
    }
  };

  const submitUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setMsg(null);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("doc_type", upForm.doc_type);
    if (upForm.name) fd.append("name", upForm.name);
    try {
      await upload(`/bidder/documents/upload`, fd);
      setUpOpen(false);
      setFile(null);
      setUpForm({ doc_type: "OTHER", name: "" });
      setMsg({ ok: true, text: "Document uploaded (marked Bidder-uploaded; government-source verification is stronger)." });
      await load();
    } catch (err: any) {
      setMsg({ ok: false, text: err?.detail ?? String(err) });
    } finally {
      setBusy(false);
    }
  };

  if (!docs) return <Loading />;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-navy-900">{t("bidder.vaultTitle")}</h1>
          <p className="text-sm text-slate-500">{t("bidder.vaultDesc")}</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={fetchDocs} loading={fetching}>
            <RefreshCw size={15} /> {t("bidder.fetchDocuments")}
          </Button>
          <Button variant="secondary" onClick={() => setUpOpen(true)}>
            <Upload size={15} /> {t("bidder.manualUpload")}
          </Button>
        </div>
      </div>

      {msg && <Alert variant={msg.ok ? "success" : "danger"}>{msg.text}</Alert>}
      <Alert variant="info">
        {t("bidder.sourceBadgeGov")} = fetched from government sources after consent. {t("bidder.sourceBadgeManual")} = uploaded by you (weaker evidence).
      </Alert>

      {docs.length === 0 ? (
        <Empty text={t("common.noData")} />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {docs.map((d) => (
            <Card key={d.id} className="flex flex-col">
              <CardContent className="flex flex-1 flex-col gap-2 p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <FileText size={18} className="shrink-0 text-slate-400" />
                    <div className="text-sm font-semibold text-slate-900">{d.name}</div>
                  </div>
                  <Badge variant={d.source_classification === "GOVERNMENT_SOURCE" ? "default" : "info"}>
                    {d.source_classification === "GOVERNMENT_SOURCE" ? t("bidder.sourceBadgeGov") : t("bidder.sourceBadgeManual")}
                  </Badge>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <Badge variant={docStatusVariant(d.verification_status)}>{d.verification_status.replace(/_/g, " ")}</Badge>
                  {d.expired && <Badge variant="danger">EXPIRED</Badge>}
                  <span>{DOC_TYPES.find((x) => x[0] === d.doc_type)?.[1] ?? d.doc_type}</span>
                </div>
                <div className="text-xs text-slate-500">
                  {d.issue_date && <>Issued {fmtDate(d.issue_date)} · </>}
                  {d.expiry_date && <>Expiry {fmtDate(d.expiry_date)} · </>}
                  {d.last_synced_at && <>Synced {fmtDateTime(d.last_synced_at)}</>}
                </div>
                {d.notes && <p className="text-xs italic text-amber-700">{d.notes}</p>}
                <div className="mt-auto flex flex-wrap gap-2 pt-2">
                  <Button size="sm" variant="secondary" onClick={() => setViewing({ kind: "bidder", docId: d.id, docName: d.name })}>
                    <Eye size={13} /> {t("bidder.viewDocument")}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => resync(d.id)} disabled={d.source === "MANUAL"}>
                    <RefreshCw size={13} /> {t("common.resync")}
                  </Button>
                  {d.file_available && <span className="self-center text-[11px] text-slate-400">File available in vault</span>}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={upOpen} onClose={() => setUpOpen(false)} title={t("bidder.manualUpload")}>
        <form onSubmit={submitUpload} className="space-y-4">
          <Field label={t("bidder.docType")} required>
            <Select value={upForm.doc_type} onChange={(e) => setUpForm({ ...upForm, doc_type: e.target.value })}>
              {DOC_TYPES.map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </Select>
          </Field>
          <Field label={t("bidder.docName")}>
            <Input value={upForm.name} onChange={(e) => setUpForm({ ...upForm, name: e.target.value })} placeholder="e.g. GST Certificate 2026" />
          </Field>
          <Field label="File (PDF/JPG/PNG ≤10MB)" required>
            <Input type="file" accept=".pdf,.jpg,.jpeg,.png" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </Field>
          <Alert variant="warning">
            Manually uploaded documents are labelled {t("bidder.sourceBadgeManual")} and can trigger {t("bidder.statusManualReview")} during verification.
          </Alert>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setUpOpen(false)}>{t("common.cancel")}</Button>
            <Button type="submit" loading={busy}>{t("common.upload")}</Button>
          </div>
        </form>
      </Dialog>

      <DocViewer target={viewing} onClose={() => setViewing(null)} />
    </div>
  );
}
