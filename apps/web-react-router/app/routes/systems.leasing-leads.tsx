import { useUser } from "@clerk/react-router";
import { Link } from "react-router";
import { ArrowDown, ArrowRight, Building2, CalendarDays, CheckCheck, Database, ExternalLink, Inbox, Settings2 } from "lucide-react";
import { Button } from "~/components/ui/button";
import { buildUrl, generateCanonicalLink, generateJsonLd, generatePageMeta } from "~/lib/seo";

const page = {
  path: "/systems/leasing-leads",
  title: "Zillow Leads Agent",
  description: "How Sernia Capital turns Zillow rental inquiries into reviewed replies: listing data, a dedicated AI agent, human approval, and a planned showing booking system.",
};
const sourceRoot = "https://github.com/EmilioEsposito/portfolio/tree/main/";
const bookingIssue = "https://github.com/EmilioEsposito/portfolio/issues/368";
const questions = [
  {
    question: "What does the Zillow Leads Agent do?",
    answer: "It reads a rental inquiry and its email history alongside reviewed listing facts and showing rules. It returns a proposed reply, no reply, or a question for the team. A person must approve every outgoing reply.",
  },
  {
    question: "How is it different from the Sernia AI general agent?",
    answer: "The general agent helps the team with operational tasks using business tools and company context. The Zillow Leads Agent has no tools. Application code prepares its input, fixes the reply recipient, and sends only after approval. Both share the same leasing policy.",
  },
  {
    question: "Where does the rental listing information come from?",
    answer: "The team maintains reviewed listings in a database through an editor, the general agent, or the Sernia MCP service. Each change has a history. The team can compare a listing with its Zillow ad on request; incoming lead replies use the saved listing, not a live scrape.",
  },
  {
    question: "Can a prospect book a showing through this system today?",
    answer: "The booking system is planned. The lead agent can include a configured booking link, but its current tour source supplies no live availability or booked showings. Calendar scheduling and resident notifications are still follow-up work for the team.",
  },
];

export const meta = () => [
  ...generatePageMeta(page),
  generateJsonLd({
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "@id": buildUrl(`${page.path}#questions`),
    mainEntity: questions.map(({ question, answer }) => ({
      "@type": "Question", name: question,
      acceptedAnswer: { "@type": "Answer", text: answer },
    })),
  }),
];
export const links = () => [generateCanonicalLink(buildUrl(page.path))];

const steps = [
  { icon: Inbox, title: "A prospect writes", description: "A Zillow inquiry reaches Gmail. Related messages wait together so the same thread is handled once at a time.", technology: "Gmail Pub/Sub · durable queue" },
  { icon: Database, title: "Build the case", description: "Code combines the shared-mailbox thread with saved listings, showing rules, and any known tour times.", technology: "Postgres · validated context" },
  { icon: Building2, title: "Decide what is owed", description: "The specialist reads the case and leasing policy. It returns a reply, no reply, or a question for the team.", technology: "PydanticAI · no tools" },
  { icon: CheckCheck, title: "Review, then send", description: "The team can approve, edit, or reject a reply. Code sends the approved text to the original Zillow relay.", technology: "Human approval · Gmail" },
];

function LeasingFlow() {
  return (
    <figure aria-labelledby="flow-title" aria-describedby="flow-caption" className="mt-6 overflow-hidden rounded-xl border">
      <div className="border-b bg-teal-50/70 px-5 py-5 dark:bg-teal-950/25 sm:px-7">
        <div className="flex items-center gap-2 text-teal-800 dark:text-teal-300">
          <Database className="size-5 shrink-0" aria-hidden="true" />
          <h3 className="font-semibold">The team maintains the facts</h3>
        </div>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
          Review the Zillow ad when needed, then save the listing through the editor, general agent,
          or MCP tools. Rent, lease dates, unit facts, booking links, and showing rules share one
          validated store with change history.
        </p>
        <p className="mt-3 flex items-center gap-2 text-sm font-medium text-teal-800 dark:text-teal-300">
          <ArrowDown className="size-4 shrink-0" aria-hidden="true" />Saved data feeds each new case
        </p>
      </div>
      <ol className="grid gap-6 px-5 py-7 sm:px-7 lg:grid-cols-4 lg:gap-8">
        {steps.map(({ icon: Icon, title, description, technology }, index) => (
          <li key={title} className="relative min-w-0">
            <div className="flex items-center gap-3">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300"><Icon className="size-5" aria-hidden="true" /></span>
              <span className="text-sm text-muted-foreground">Step {index + 1}</span>
              {index < steps.length - 1 && <ArrowRight className="ml-auto hidden size-5 text-muted-foreground lg:block" aria-hidden="true" />}
            </div>
            <h3 className="mt-4 font-semibold">{title}</h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{description}</p>
            <p className="mt-4 text-xs leading-5 text-muted-foreground">{technology}</p>
            {index < steps.length - 1 && <ArrowDown className="mt-5 size-5 text-muted-foreground lg:hidden" aria-hidden="true" />}
          </li>
        ))}
      </ol>
      <div className="grid gap-3 border-t bg-muted/30 px-5 py-5 text-sm sm:grid-cols-3 sm:px-7">
        <p><strong className="font-semibold">Reply</strong><br /><span className="leading-6 text-muted-foreground">Waits for approval before sending.</span></p>
        <p><strong className="font-semibold">No reply</strong><br /><span className="leading-6 text-muted-foreground">Decision saved; nothing sent.</span></p>
        <p><strong className="font-semibold">Escalate</strong><br /><span className="leading-6 text-muted-foreground">A question goes to human review.</span></p>
      </div>
      <div className="m-5 rounded-lg border border-dashed border-amber-600/60 bg-amber-50/60 p-4 dark:bg-amber-950/20 sm:m-7">
        <h3 className="flex items-center gap-2 font-semibold text-amber-900 dark:text-amber-200"><CalendarDays className="size-5 shrink-0" aria-hidden="true" />Planned: showing bookings</h3>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
          Showing rules + shared calendars → available times → prospect books → calendar events
          and resident notice. Availability and booked showings will feed back into the case,
          so the agent can offer an existing showing first.
        </p>
        <a href="#booking" className="mt-3 inline-block text-sm underline underline-offset-4">See what is still being built</a>
      </div>
      <figcaption id="flow-caption" className="border-t px-5 py-4 text-xs leading-5 text-muted-foreground sm:px-7">
        The solid sections describe the implemented lead-reply flow. The dashed section is the planned
        booking integration. Today, the live tour source returns no open times or booked showings.
      </figcaption>
    </figure>
  );
}

export default function ZillowLeadsPage() {
  const { user } = useUser();
  const isSernia = user?.emailAddresses?.some(
    (email) => email.emailAddress.endsWith("@serniacapital.com") && email.verification?.status === "verified",
  );
  return (
    <main className="mx-auto w-full max-w-6xl px-5 py-9 sm:px-8 sm:py-14">
      <Link to="/sernia-capital" className="text-sm text-muted-foreground underline underline-offset-4">Sernia Capital systems</Link>
      <header className="mt-8 max-w-3xl">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">Zillow Leads Agent</h1>
        <p className="mt-5 text-xl leading-8">From rental inquiry to a reviewed reply.</p>
        <p className="mt-4 leading-7 text-muted-foreground">
          A prospect asks about an apartment. The agent reads the conversation and the team’s
          reviewed listing data, then proposes a reply, stays quiet, or asks for help. Every outgoing
          reply waits for a person. Showing bookings are the next part of the system.
        </p>
        <p className="mt-4 text-sm leading-6 text-muted-foreground">
          Built by Emilio Esposito for Sernia Capital’s rental operations. This is an independent
          integration with Zillow inquiry emails, not a Zillow product.
        </p>
      </header>
      {isSernia && (
        <nav aria-label="Zillow Leads Agent management" className="mt-7 flex flex-wrap gap-3 rounded-lg border bg-muted/30 p-4">
          <Button asChild><Link to="/sernia-rental-listings"><Building2 className="mr-2 size-4" aria-hidden="true" />Rental listings &amp; showing rules</Link></Button>
          <Button asChild variant="outline"><Link to="/sernia-settings#zillow-leads"><Settings2 className="mr-2 size-4" aria-hidden="true" />Lead trigger settings</Link></Button>
          <Button asChild variant="outline"><Link to="/sernia-chat">Conversations &amp; approvals</Link></Button>
        </nav>
      )}
      <section aria-labelledby="flow-title" className="mt-12">
        <h2 id="flow-title" className="text-2xl font-semibold tracking-tight">How an inquiry moves through the business</h2>
        <LeasingFlow />
      </section>
      <div className="mt-12 grid gap-9 lg:grid-cols-2 lg:gap-12">
        <section aria-labelledby="listing-data">
          <h2 id="listing-data" className="text-xl font-semibold">One reviewed listing, several ways to maintain it</h2>
          <p className="mt-4 leading-7 text-muted-foreground">
            The rental-listings editor, the general agent, and the Sernia MCP service all use the
            same validated listing service. Every saved change records who changed what and when.
            Asking rent and the next lease dates are separate from the current resident’s lease.
          </p>
          <p className="mt-4 leading-7 text-muted-foreground">
            A Zillow-ad check is available on request to help the team compare the public ad with
            the saved facts. It does not silently update the listing. The lead agent reads the
            reviewed data, so replies do not depend on a live Zillow fetch succeeding.
          </p>
        </section>
        <section aria-labelledby="agent-boundary">
          <h2 id="agent-boundary" className="text-xl font-semibold">A specialist with a small job</h2>
          <p className="mt-4 leading-7 text-muted-foreground">
            This agent has no tools and cannot search the company’s inbox, edit listings, or send
            messages itself. Code builds its case and fixes the reply address before the model runs.
            Approval sends the reviewed text without asking the model to write it again.
          </p>
          <p className="mt-4 leading-7 text-muted-foreground">
            The <Link to="/systems/sernia-ai" className="text-foreground underline underline-offset-4">Sernia AI general agent</Link>{" "}
            handles broader team requests with business tools and persistent context. It can maintain
            listings and consult the same leasing policy. Incoming Zillow leads go to the specialist.
          </p>
        </section>
      </div>
      <section id="booking" aria-labelledby="booking-title" className="mt-12 scroll-mt-8 border-t pt-9">
        <div className="flex flex-wrap items-center gap-3">
          <h2 id="booking-title" className="text-2xl font-semibold tracking-tight">The booking system</h2>
          <span className="rounded-md border border-dashed border-amber-600/60 px-2 py-1 text-xs text-amber-800 dark:text-amber-200">Planned</span>
        </div>
        <p className="mt-4 max-w-3xl leading-7 text-muted-foreground">
          The next step is a booking page where a prospect chooses a showing without an email
          exchange. The direction is tracked in <a href={bookingIssue} className="text-foreground underline underline-offset-4">issue #368</a>;
          these connections are planned, not active booking features.
        </p>
        <ol className="mt-6 grid gap-7 md:grid-cols-3">
          <li><h3 className="font-semibold">1. Find times that work</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Start with the showing window and blackout dates, subtract busy time on shared calendars, and require at least 24 hours’ notice. Unreadable calendars must not count as free.</p></li>
          <li><h3 className="font-semibold">2. Book or join a showing</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">A booking link lets the prospect choose a time. Existing showings and preferred times help group visits and reduce disruption for the resident.</p></li>
          <li><h3 className="font-semibold">3. Coordinate the visit</h3><p className="mt-2 text-sm leading-6 text-muted-foreground">Create calendar events for the prospect and showing agent, block the time, and notify the current resident through the existing messaging gates.</p></li>
        </ol>
        <p className="mt-6 max-w-3xl text-sm leading-6 text-muted-foreground">
          Today, the tour-source interface is in place but supplies no live times. A configured booking
          link can appear in a reply; without one, the agent leaves follow-up to the team. Recording a
          confirmed tour in an outcome does not create a booking, calendar event, or phone contact.
        </p>
      </section>
      <section aria-labelledby="evaluation-title" className="mt-12 border-t pt-9">
        <h2 id="evaluation-title" className="text-xl font-semibold">Evaluate the same decision that runs in production</h2>
        <p className="mt-4 max-w-3xl leading-7 text-muted-foreground">
          Offline evaluations replay a conversation at the moment a response was owed, with the
          listing data for that case. They call the same response function as the live queue.
          Exact checks, human review, and an LLM judge help locate failures in data, policy, or code.
          Applicant emails and private evaluation files stay out of the public site.
        </p>
        <Link to="/systems/leasing-evals" className="mt-4 inline-block text-sm underline underline-offset-4">Explore the evaluation process</Link>
      </section>
      <section id="questions" aria-labelledby="questions-title" className="mt-12 border-t pt-9">
        <h2 id="questions-title" className="text-xl font-semibold">Common questions</h2>
        <dl className="mt-6 grid gap-7 md:grid-cols-2 md:gap-x-12">
          {questions.map(({ question, answer }) => <div key={question}><dt className="font-medium">{question}</dt><dd className="mt-2 text-sm leading-6 text-muted-foreground">{answer}</dd></div>)}
        </dl>
      </section>
      <details className="mt-12 rounded-lg border p-5">
        <summary className="cursor-pointer font-medium">Implementation map</summary>
        <p className="mt-4 text-sm leading-6 text-muted-foreground">FastAPI coordinates the workflow; PydanticAI runs the specialist through OpenRouter. PostgreSQL stores listings, queue state, and conversations. APScheduler runs the queue worker; Logfire records execution.</p>
        <ul className="mt-4 space-y-3 text-sm">
          {[
            ["api/src/sernia_ai/zillow_leads", "Lead agent, case builder, approvals, and evaluations"],
            ["api/src/rental_listings", "Rental listing data, validation, history, and API"],
            ["api/src/sernia_ai/triggers", "Email queue and trigger lifecycle"],
            ["api/src/sernia_ai", "General agent and shared operational services"],
          ].map(([path, label]) => <li key={path}><a href={`${sourceRoot}${path}`} className="inline-flex items-center gap-2 underline underline-offset-4">{label}<ExternalLink className="size-3 shrink-0" aria-hidden="true" /></a></li>)}
        </ul>
      </details>
    </main>
  );
}
