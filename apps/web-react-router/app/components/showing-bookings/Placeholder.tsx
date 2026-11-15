import type { ReactNode } from "react";

/** A section of the Booking settings tab that a later step of the booking work fills. */
export function PlaceholderSection({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="rounded-xl border border-dashed p-5 sm:p-6">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id={id} className="text-lg font-semibold">
          {title}
        </h3>
        <span className="rounded border border-dashed px-1.5 py-0.5 text-[10px] text-muted-foreground">Coming next</span>
      </div>
      <p className="mt-2 text-sm leading-6 text-muted-foreground">{children}</p>
    </section>
  );
}
