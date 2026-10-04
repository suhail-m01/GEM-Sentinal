/** Pure-CSS page skeleton: shown by Next.js while a route's data is being
 *  prepared, so switching between features never shows a white screen. */
export function PageLoading() {
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading">
      <div className="h-9 w-48 animate-pulse rounded-md bg-slate-200" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded-lg bg-slate-200/70" />
        ))}
      </div>
      <div className="h-80 animate-pulse rounded-lg bg-slate-200/50" />
    </div>
  );
}
