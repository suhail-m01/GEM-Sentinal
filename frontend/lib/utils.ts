import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatINR(n: number | null | undefined): string {
  if (n === null || n === undefined) return "-";
  return "₹" + n.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

export function fmtDate(d: string | null | undefined): string {
  if (!d) return "-";
  try {
    return new Date(d).toLocaleDateString("en-IN", { year: "numeric", month: "short", day: "numeric" });
  } catch {
    return d;
  }
}

export function fmtDateTime(d: string | null | undefined): string {
  if (!d) return "-";
  try {
    return new Date(d).toLocaleString("en-IN", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return d;
  }
}

/* ---------- Money (exact, no float) ----------
 * Quoted amounts are handled as integer paise parsed from the user's decimal
 * string, so the browser never does float arithmetic on money. The server
 * re-validates with Decimal and remains authoritative; this is display +
 * client-side guardrail only.
 * ponytail: integer paise is enough for a 2dp currency sum. If multi-currency
 * minor units (e.g. 3dp) are ever needed, carry the exponent alongside.
 */
export function parsePaise(s: string): bigint | null {
  const v = (s ?? "").trim();
  if (!v) return null;
  if (!/^\d{1,15}(\.\d{1,2})?$/.test(v)) return null;
  const [whole, frac = ""] = v.split(".");
  return BigInt(whole) * 100n + BigInt(frac.padEnd(2, "0"));
}

export function formatPaise(p: bigint): string {
  const neg = p < 0n;
  const abs = neg ? -p : p;
  const rupees = abs / 100n;
  const paise = (abs % 100n).toString().padStart(2, "0");
  return (neg ? "-" : "") + "₹" + rupees.toLocaleString("en-IN") + "." + paise;
}

/** Format a server decimal string (e.g. "9500000.00") as INR, exactly. */
export function formatAmount(s: string | null | undefined): string {
  if (s === null || s === undefined || s === "") return "-";
  const p = parsePaise(s);
  return p === null ? s : formatPaise(p);
}
