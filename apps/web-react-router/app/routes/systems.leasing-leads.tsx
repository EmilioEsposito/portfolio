import { useUser } from "@clerk/react-router";
import { Link } from "react-router";
import { Building2, Settings2 } from "lucide-react";
import LeasingArchitecture from "~/components/leasing-architecture";
import { Button } from "~/components/ui/button";
import { buildUrl, generateCanonicalLink, generatePageMeta } from "~/lib/seo";

const page = {
  path: "/systems/leasing-leads",
  title: "Zillow Leads Agent",
  description: "Sernia Capital’s leasing architecture: live Zillow listing updates, a focused AI reply agent, shared-calendar availability, and showing bookings.",
};

export const meta = () => generatePageMeta(page);
export const links = () => [generateCanonicalLink(buildUrl(page.path))];

export default function ZillowLeadsPage() {
  const { user } = useUser();
  const isSernia = user?.emailAddresses?.some(
    (email) => email.emailAddress.endsWith("@serniacapital.com") && email.verification?.status === "verified",
  );

  return (
    <main className="mx-auto w-full max-w-7xl px-5 py-9 sm:px-8 sm:py-12">
      <Link to="/sernia-capital" className="text-sm text-muted-foreground underline underline-offset-4">Sernia Capital systems</Link>
      <header className="mb-7 mt-6 max-w-3xl">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">Zillow Leads Agent</h1>
        <p className="mt-4 text-lg leading-8 text-muted-foreground">
          A leasing system for Sernia Capital: keep listing facts current, answer rental inquiries,
          and coordinate showings across multiple agents. The goal is to handle routine conversations
          and bookings automatically, with the team handling exceptions.
        </p>
      </header>

      {isSernia && (
        <nav aria-label="Zillow Leads Agent management" className="mb-6 flex flex-wrap gap-3">
          <Button asChild><Link to="/sernia-rental-listings"><Building2 className="mr-2 size-4" aria-hidden="true" />Rental listings &amp; showing rules</Link></Button>
          <Button asChild variant="outline"><Link to="/sernia-settings#zillow-leads"><Settings2 className="mr-2 size-4" aria-hidden="true" />Lead trigger settings</Link></Button>
          <Button asChild variant="outline"><Link to="/sernia-chat">Agent conversations</Link></Button>
        </nav>
      )}

      <LeasingArchitecture />

      <section aria-labelledby="agents-title" className="mt-7 grid gap-3 sm:grid-cols-[12rem_1fr] sm:gap-5">
        <h2 id="agents-title" className="font-semibold">Two agents, different access</h2>
        <div className="max-w-3xl space-y-3 text-sm leading-6 text-muted-foreground">
          <p>
            The <Link to="/systems/sernia-ai" className="text-foreground underline underline-offset-4">general agent</Link>{" "}
            can fetch live Zillow ads and update the listing table. The lead agent receives only the
            conversation, listing facts, and showing context; it has no broad tools to retrieve
            sensitive company data. This separation supports automatic replies.
          </p>
          <p className="text-xs">Replies currently pass through approval during rollout.</p>
        </div>
      </section>

      <footer className="mt-7 space-y-4 border-t pt-5 text-sm">
        <p className="text-xs leading-6 text-muted-foreground">FastAPI orchestrates the flow, PydanticAI runs the agent, and Postgres stores listings and conversations. Gmail carries replies; Google Calendar supplies showing availability in the booking design.</p>
        <div className="flex flex-wrap gap-x-6 gap-y-3">
          <Link to="/systems/leasing-evals" className="underline underline-offset-4">How replies are evaluated</Link>
          <a href="https://github.com/EmilioEsposito/portfolio/tree/main/api/src/sernia_ai/zillow_leads" className="underline underline-offset-4">Lead agent source</a>
          <a href="https://github.com/EmilioEsposito/portfolio/tree/main/api/src/rental_listings" className="underline underline-offset-4">Listing data &amp; tools</a>
        </div>
      </footer>
    </main>
  );
}
