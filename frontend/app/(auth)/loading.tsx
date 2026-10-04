import { Loader2 } from "lucide-react";

export default function Loading() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <Loader2 size={28} className="animate-spin text-navy-700" />
    </div>
  );
}
