import Link from "next/link";

export function LegalShell({ title, updated, children }: { title: string; updated: string; children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-slate-200 bg-navy-900">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-5 py-4">
          <Link href="/" className="text-lg font-bold text-white">
            GeM-Sentinel
          </Link>
          <nav className="flex gap-4 text-sm text-navy-200">
            <Link href="/privacy" className="hover:text-white">
              Privacy Policy
            </Link>
            <Link href="/terms" className="hover:text-white">
              Terms of Use
            </Link>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-5 py-10">
        <h1 className="text-3xl font-bold text-navy-900">{title}</h1>
        <p className="mt-2 text-sm text-slate-500">Last updated: {updated}</p>
        <div className="mt-8 space-y-8 text-sm leading-6 text-slate-700">{children}</div>
      </main>
    </div>
  );
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="text-base font-semibold text-navy-900">{title}</h2>
      <div className="mt-2 space-y-2">{children}</div>
    </section>
  );
}

export function List({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="ml-5 list-disc space-y-1">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ul>
  );
}
