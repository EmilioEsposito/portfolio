import { Link } from "react-router";
import {
  ArrowDown,
  ArrowLeftRight,
  CalendarClock,
  MessageSquare,
  Globe,
  Brain,
  Database,
  FolderSearch,
  Calendar,
  Mail,
  ShieldCheck,
  Code,
  Minimize2,
  Search,
  FileDown,
} from "lucide-react";
import {
  operationsStudy,
  caseStudyMeta,
  caseStudyLinks,
} from "~/components/operational-case-study";

export const meta = () => caseStudyMeta(operationsStudy);
export const links = () => caseStudyLinks(operationsStudy);

function Connection({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-center gap-2 py-4 text-xs text-muted-foreground">
      <ArrowDown aria-hidden="true" className="size-4" />
      {children}
    </div>
  );
}

const triggers = [
  {
    icon: CalendarClock,
    title: "Scheduled run",
    text: "Proactive checks and follow-ups",
  },
  {
    icon: MessageSquare,
    title: "SMS webhook",
    text: "Internal team messages on the AI line",
  },
  { icon: Globe, title: "Web chat", text: "Authenticated team conversations" },
];
const tools = [
  {
    icon: FolderSearch,
    title: "Google Drive",
    text: "Search and read company files",
  },
  {
    icon: Calendar,
    title: "Google Calendar",
    text: "Read schedules; create or delete events",
  },
  { icon: Mail, title: "Gmail", text: "Search threads, read and send email" },
  {
    icon: MessageSquare,
    title: "Quo SMS",
    text: "Read conversations and send texts",
  },
];

const capabilities = [
  {
    icon: Code,
    title: "Monty Python sandbox",
    text: "Calculations and data transformations through run_python, without filesystem or network access.",
  },
  {
    icon: Minimize2,
    title: "Compaction",
    text: "Automatically summarizes large tool results and older conversation history before model requests.",
  },
  {
    icon: Search,
    title: "WebSearch",
    text: "Provider-native search, restricted to allowed domains when supported by the selected model.",
  },
  {
    icon: FileDown,
    title: "WebFetch",
    text: "Provider-native page retrieval on supported models. No local fallback when unavailable.",
  },
];

export default function CaseStudyPage() {
  return (
    <main className="mx-auto w-full max-w-6xl px-5 py-10 sm:px-8 sm:py-14">
      <Link
        to="/#sernia-systems"
        className="text-sm text-muted-foreground underline underline-offset-4"
      >
        Back to Sernia Capital
      </Link>
      <header className="mt-8 max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-5xl">
          SerniaAI operations agent
        </h1>
        <p className="mt-5 text-lg leading-8 text-muted-foreground">
          One agent across team conversations and scheduled work, with shared
          company memory and access to the tools we use to run Sernia Capital.
        </p>
      </header>

      <figure
        aria-labelledby="architecture-title"
        aria-describedby="architecture-caption"
        className="mt-10 rounded-2xl border p-4 sm:p-7"
      >
        <h2 id="architecture-title" className="mb-6 text-xl font-semibold">
          How the system connects
        </h2>
        <div className="grid gap-3 sm:grid-cols-3">
          {triggers.map(({ icon: Icon, title, text }) => (
            <div key={title} className="rounded-lg border bg-muted/30 p-4">
              <Icon
                aria-hidden="true"
                className="mb-3 size-5 text-muted-foreground"
              />
              <h3 className="font-medium">{title}</h3>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                {text}
              </p>
            </div>
          ))}
        </div>
        <Connection>Each starts a run with its conversation context</Connection>
        <div className="grid items-stretch gap-3 lg:grid-cols-[1fr_auto_1fr]">
          <div className="rounded-xl border border-sky-700/40 bg-sky-50 p-5 dark:bg-sky-950/30">
            <Brain
              aria-hidden="true"
              className="mb-3 size-6 text-sky-700 dark:text-sky-300"
            />
            <h3 className="text-xl font-semibold">SerniaAI</h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              Gathers context, decides what needs doing, and chooses tools. It
              can act, reply, schedule a follow-up, or leave things alone.
            </p>
          </div>
          <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground lg:flex-col">
            <ArrowLeftRight
              aria-hidden="true"
              className="size-5 rotate-90 lg:rotate-0"
            />
            <span>Read &amp; write</span>
          </div>
          <div className="rounded-xl border bg-muted/30 p-5">
            <Database
              aria-hidden="true"
              className="mb-3 size-6 text-muted-foreground"
            />
            <h3 className="text-lg font-semibold">Shared company memory</h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              Property knowledge, daily notes, procedures, and reusable skills
              in a persistent, versioned workspace.
            </p>
            <p className="mt-3 text-sm font-medium">
              The same knowledge across people, conversations, and future runs.
            </p>
          </div>
        </div>
        <Connection>
          The agent selects tools; application code checks each action
        </Connection>
        <div className="grid gap-4 md:grid-cols-2">
          {[
            {
              title: "Agent capabilities",
              description: "Computation, research, and context management",
              items: capabilities,
            },
            {
              title: "Business integrations",
              description:
                "Access to company information and operational actions",
              items: tools,
            },
          ].map(({ title, description, items }) => (
            <section key={title} className="min-w-0 rounded-xl border p-5">
              <h3 className="text-lg font-semibold">{title}</h3>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                {description}
              </p>
              <div className="mt-4 divide-y">
                {items.map(({ icon: Icon, title: itemTitle, text }) => (
                  <div key={itemTitle} className="py-4">
                    <div className="flex items-center gap-2">
                      <Icon
                        aria-hidden="true"
                        className="size-4 shrink-0 text-muted-foreground"
                      />
                      <h4 className="text-sm font-semibold">{itemTitle}</h4>
                    </div>
                    <p className="mt-2 text-sm leading-6 text-muted-foreground">
                      {text}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
        <div className="mt-4 space-y-2 text-sm leading-6 text-muted-foreground">
          <p>
            These groups describe purpose, not separate permission levels. Code
            execution and web access are tools too; compaction runs
            automatically to manage context.
          </p>
          <p>
            <span className="font-medium text-foreground">
              BashTool is deliberately excluded.
            </span>{" "}
            The agent is not given a general-purpose Bash tool for host shell
            commands.
          </p>
        </div>
        <Connection>
          Permission depends on the action and recipient, not just the tool
        </Connection>
        <div className="grid gap-4 md:grid-cols-2">
          <section className="rounded-xl border border-emerald-700/40 bg-emerald-50 p-5 dark:bg-emerald-950/25">
            <h3 className="text-lg font-semibold text-emerald-900 dark:text-emerald-200">
              Internal work: act autonomously
            </h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              No per-action approval for routine work within the team.
            </p>
            <ul className="mt-4 list-disc space-y-2 pl-5 text-sm leading-6">
              <li>Search files, email, calendars, and SMS history</li>
              <li>Read and update shared memory</li>
              <li>Send internal emails and texts</li>
              <li>Manage internal-only calendar events</li>
              <li>Create and update tasks; coordinate follow-ups</li>
            </ul>
            <div className="mt-5 border-t border-emerald-700/20 pt-4 text-sm font-medium">
              Execute and return the result to the agent
            </div>
          </section>
          <section className="rounded-xl border border-amber-700/40 bg-amber-50 p-5 dark:bg-amber-950/25">
            <h3 className="text-lg font-semibold text-amber-900 dark:text-amber-200">
              External communications: request approval
            </h3>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              The standard path for messages to tenants, vendors, and other
              external contacts.
            </p>
            <ul className="mt-4 list-disc space-y-2 pl-5 text-sm leading-6">
              <li>External SMS and email</li>
              <li>Calendar changes involving external attendees</li>
            </ul>
            <div className="mt-5 flex items-center gap-2 rounded-lg border border-amber-700/30 bg-background/70 p-3 text-sm font-semibold">
              <ShieldCheck aria-hidden="true" className="size-5 shrink-0" />
              Human-in-the-loop review
            </div>
            <p className="mt-3 text-sm leading-6">
              Approve to execute. Reject to stop the proposed action.
            </p>
          </section>
        </div>
        <figcaption
          id="architecture-caption"
          className="mt-6 text-sm leading-6 text-muted-foreground"
        >
          Shared memory carries knowledge between runs; individual conversations
          retain their own history. Tool results return to the agent so it can
          continue working. The diagram highlights the main entry points and
          integrations.
        </figcaption>
      </figure>

      <section
        className="mt-9 grid gap-5 border-b pb-9 sm:grid-cols-[220px_1fr]"
        aria-labelledby="boundaries-title"
      >
        <h2 id="boundaries-title" className="text-lg font-semibold">
          Where the boundaries sit
        </h2>
        <div className="space-y-4 text-sm leading-7 text-muted-foreground">
          <p>
            Approval is enforced by the tools, not left to the model’s judgment.
            Some sensitive internal changes also require review, including task
            deletion and contact updates or deletion.
          </p>
          <p>
            There are narrow external automation paths: fixed, code-owned
            reminders can send without approval, with recipient eligibility
            checks and no model-written message text. Leasing email automation
            also has an operator-controlled approval setting. These are separate
            from the standard approval path shown above.
          </p>
          <p>
            Automated triggers have a kill switch and rate limits. The agent
            works within authenticated access and tool permissions.
          </p>
        </div>
      </section>
      <footer className="mt-7 flex flex-wrap gap-x-6 gap-y-3 text-sm">
        <a
          href={operationsStudy.source}
          className="underline underline-offset-4"
        >
          Read the implementation
        </a>
        <Link to="/multi-agent-chat" className="underline underline-offset-4">
          Explore the public agent demo
        </Link>
        <p className="w-full leading-6 text-muted-foreground">
          This is the private operational system. The public demo has separate,
          restricted tools and no access to company records.
        </p>
      </footer>
    </main>
  );
}
