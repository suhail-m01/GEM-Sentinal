import { getRequestConfig } from "next-intl/server";
import { cookies, headers } from "next/headers";

function detectLocale(): "en" | "hi" {
  const cookie = cookies().get("NEXT_LOCALE")?.value;
  if (cookie === "hi" || cookie === "en") return cookie;
  const lang = headers().get("accept-language")?.toLowerCase();
  return lang?.startsWith("hi") ? "hi" : "en";
}

export default getRequestConfig(async () => {
  const locale = detectLocale();
  return {
    locale,
    messages: (await import(`./locales/${locale}.json`)).default,
  };
});
