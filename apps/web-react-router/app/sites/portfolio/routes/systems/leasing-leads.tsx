import { Link } from "react-router";
import { LeasingOverview } from "~/components/leasing-overview";
import { buildUrl, generateCanonicalLink, generatePageMeta } from "~/sites/portfolio/lib/seo";
import { TEAM_HOME_URL } from "~/sites/site";

const page = {
  path: "/systems/leasing-leads",
  title: "Zillow Leads Agent",
  description: "Sernia Capital’s leasing architecture: live Zillow listing updates, a focused AI reply agent, showings booked on a page or by email, and who is told about each one.",
};

export const meta = () => generatePageMeta(page);
export const links = () => [generateCanonicalLink(buildUrl(page.path))];

export default function ZillowLeadsPage() {
  return (
    <main className="mx-auto w-full max-w-7xl px-5 py-9 sm:px-8 sm:py-12">
      <Link to="/sernia-capital" className="text-sm text-muted-foreground underline underline-offset-4">Sernia Capital systems</Link>
      <header className="mb-6 mt-6">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">Zillow Leads Agent</h1>
      </header>
      <p className="mb-7 max-w-3xl text-lg leading-8 text-muted-foreground">
        A leasing system for Sernia Capital: keep listing facts current, answer rental inquiries,
        and coordinate showings across multiple agents. Routine conversations and bookings are
        handled automatically, and the team handles exceptions.
      </p>
      <LeasingOverview />

      <section aria-labelledby="agents-title" className="mt-9 grid gap-3 sm:grid-cols-[12rem_1fr] sm:gap-5">
        <h2 id="agents-title" className="font-semibold">Two agents, different access</h2>
        <div className="max-w-3xl space-y-3 text-sm leading-6 text-muted-foreground">
          <p>
            The <Link to="/systems/sernia-ai" className="text-foreground underline underline-offset-4">general agent</Link>{" "}
            can fetch live Zillow ads and update the listing table. The lead agent receives only the
            conversation, listing facts, and showing context; it has no broad tools to retrieve
            sensitive company data. This separation supports automatic replies.
          </p>
        </div>
      </section>

      <footer className="mt-7 space-y-4 border-t pt-5 text-sm">
        <p className="text-xs leading-6 text-muted-foreground">FastAPI orchestrates the flow, PydanticAI runs the agent, and Postgres stores listings, conversations and showings. Gmail carries replies, Google Calendar supplies availability and holds the events, and Quo sends the texts.</p>
        <div className="flex flex-wrap gap-x-6 gap-y-3">
          <Link to="/systems/leasing-evals" className="underline underline-offset-4">How replies are evaluated</Link>
          <a href="https://github.com/EmilioEsposito/portfolio/tree/main/api/src/zillow_leads" className="underline underline-offset-4">Lead agent source</a>
          <a href="https://github.com/EmilioEsposito/portfolio/tree/main/api/src/showing_bookings" className="underline underline-offset-4">Booking source</a>
          <a href="https://github.com/EmilioEsposito/portfolio/tree/main/api/src/rental_listings" className="underline underline-offset-4">Listing data &amp; tools</a>
        </div>
        <p className="text-xs text-muted-foreground">
          Sernia Capital staff manage listings, settings and bookings on the{" "}
          <a href={TEAM_HOME_URL} className="underline underline-offset-4">team site</a>.
        </p>
      </footer>
    </main>
  );
}
