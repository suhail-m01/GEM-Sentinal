"use client";

import React, { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Download, FileImage } from "lucide-react";
import { rawRequest, downloadFile, getTokens } from "@/lib/api";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Loading } from "@/components/app/widgets";

export interface DocViewerTarget {
  kind: "bidder" | "officer";
  docId: string;
  docName: string;
  applicationId?: string; // officer view is gated per application
}

/**
 * Renders an original-looking replica of a document (Document Studio).
 * Bidders see their own replica; officers see a "manual review copy" (gated
 * on the bid deadline server-side). Every image carries a verified-record
 * seal and a SHA-256 content hash.
 */
export function DocViewer({
  target,
  onClose,
}: {
  target: DocViewerTarget | null;
  onClose: () => void;
}) {
  const t = useTranslations();
  const [url, setUrl] = useState<string | null>(null);
  const [hash, setHash] = useState("");
  const [err, setErr] = useState("");
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    let obj: string | null = null;
    let cancelled = false;
    if (!target) return;
    setUrl(null);
    setErr("");
    setHash("");
    (async () => {
      try {
        const path =
          target.kind === "bidder"
            ? `/bidder/documents/${target.docId}/view`
            : `/officer/documents/${target.docId}/view?application_id=${target.applicationId}`;
        const res = await rawRequest(path);
        if (!res.ok) {
          let det: unknown = await res.text();
          try {
            det = JSON.parse(det as string);
          } catch {
            /* keep */
          }
          throw new Error(typeof det === "string" ? det : (det as any)?.message ?? JSON.stringify(det));
        }
        const blob = await res.blob();
        obj = URL.createObjectURL(blob);
        if (cancelled) {
          URL.revokeObjectURL(obj);
          return;
        }
        setUrl(obj);
        setHash(res.headers.get("x-document-hash") ?? "");
      } catch (e: any) {
        if (!cancelled) setErr(e?.message ?? String(e));
      }
    })();
    return () => {
      cancelled = true;
      if (obj) URL.revokeObjectURL(obj);
    };
  }, [target?.docId, target?.kind, target?.applicationId]);

  const download = async () => {
    if (!target) return;
    setDownloading(true);
    try {
      const path =
        target.kind === "bidder"
          ? `/bidder/documents/${target.docId}/view`
          : `/officer/documents/${target.docId}/view?application_id=${target.applicationId}`;
      await downloadFile(path, `replica-${target.docName}.png`);
    } catch {
      /* ignore */
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Dialog open={target !== null} onClose={onClose} title={target ? target.docName : ""} wide>
      {target && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            {target.kind === "officer" && <Badge variant="critical">{t("docViewer.reviewCopy")}</Badge>}
            <span className="text-xs text-slate-500">{t("docViewer.desc")}</span>
          </div>

          <div className="flex max-h-[68vh] items-center justify-center overflow-auto rounded-md border border-slate-200 bg-slate-50 p-2">
            {err ? (
              <p className="p-6 text-sm text-red-700">{err}</p>
            ) : !url ? (
              <div className="p-10">
                <Loading />
              </div>
            ) : (
              <img src={url} alt={target.docName} className="max-w-full" style={{ minHeight: "40vh" }} />
            )}
          </div>

          {hash && (
            <p className="font-mono text-[10px] text-slate-400">
              SHA-256 {hash}
            </p>
          )}

          <div className="flex justify-end gap-2">
            {url && (
              <Button size="sm" variant="outline" onClick={download} loading={downloading}>
                <Download size={13} /> {t("docViewer.download")}
              </Button>
            )}
            <Button size="sm" variant="ghost" onClick={onClose}>
              {t("common.close")}
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
