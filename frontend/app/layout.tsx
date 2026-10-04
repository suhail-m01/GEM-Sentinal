import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { cookies } from "next/headers";
import { NextIntlClientProvider } from "next-intl";
import { getMessages, setRequestLocale } from "next-intl/server";
import { AuthProvider } from "@/lib/auth";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "GeM-Sentinel | Verify Once. Trust Always.",
  description:
    "Integrated bid compliance verification for GeM procurement (Ministry of P&G / CPCL). Deterministic rules decide; AI only explains.",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const cookieLocale = cookies().get("NEXT_LOCALE")?.value;
  const locale = cookieLocale === "hi" ? "hi" : "en";
  await setRequestLocale(locale);
  const messages = await getMessages();
  return (
    <html lang={locale} className={inter.variable}>
      <body>
        <NextIntlClientProvider locale={locale} messages={messages}>
          <AuthProvider>{children}</AuthProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
