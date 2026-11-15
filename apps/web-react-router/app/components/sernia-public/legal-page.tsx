import { SERNIA_CONTACT_EMAIL } from "~/lib/sernia-public-seo";

/** A policy page under the Sernia layout: title, date and sections. */
export function LegalPage({
  title,
  updated,
  children,
}: {
  title: string;
  updated: string;
  children: React.ReactNode;
}) {
  return (
    <article>
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{title}</h1>
      <p className="mt-2 text-sm text-muted-foreground">Last updated {updated}</p>
      <div className="mt-8 space-y-8">{children}</div>
    </article>
  );
}

export function LegalSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
      <div className="space-y-3 leading-7 [&_ul]:list-disc [&_ul]:space-y-1 [&_ul]:pl-5">{children}</div>
    </section>
  );
}

export function ContactEmail() {
  return (
    <a href={`mailto:${SERNIA_CONTACT_EMAIL}`} className="font-medium underline underline-offset-4">
      {SERNIA_CONTACT_EMAIL}
    </a>
  );
}
