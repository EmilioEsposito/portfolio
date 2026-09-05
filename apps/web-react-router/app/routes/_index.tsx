import type { Route } from "./+types/_index";
import { H2, P } from "~/components/typography";
import { Link } from "react-router";
import { Badge } from "~/components/ui/badge";
import {
  SITE_OWNER,
  DEFAULT_META,
  buildUrl,
  generateOgMeta,
  generateJsonLd,
  generateCanonicalLink,
} from "~/lib/seo";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "~/components/ui/card";
import {
  ExternalLink,
  FileText,
  Bot,
  Mail,
  Calendar,
  MessageSquare,
  AlertTriangle,
} from "lucide-react";

function GitHubIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
      <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0 1 12 6.844a9.59 9.59 0 0 1 2.504.337c1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.02 10.02 0 0 0 22 12.017C22 6.484 17.522 2 12 2Z" />
    </svg>
  );
}

function LinkedInIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
      <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286ZM5.337 7.433a2.062 2.062 0 0 1-2.063-2.065 2.064 2.064 0 1 1 2.063 2.065Zm1.782 13.019H3.555V9h3.564v11.452ZM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003Z" />
    </svg>
  );
}

export function meta(_args: Route.MetaArgs) {
  const url = buildUrl("/");
  return [
    { title: SITE_OWNER },
    { name: "description", content: DEFAULT_META.description },
    ...generateOgMeta({
      title: SITE_OWNER,
      description: DEFAULT_META.description,
      url,
      type: "profile",
    }),
    generateJsonLd({
      "@context": "https://schema.org",
      "@type": "Person",
      name: "Emilio Esposito",
      jobTitle: "Senior Director, AI Engineering & Enablement",
      worksFor: {
        "@type": "Organization",
        name: "LegalZoom",
      },
      founder: {
        "@type": "Organization",
        name: "Sernia Capital",
      },
      alumniOf: [
        {
          "@type": "CollegeOrUniversity",
          name: "Carnegie Mellon University",
        },
        {
          "@type": "CollegeOrUniversity",
          name: "Penn State University",
        },
      ],
      sameAs: [
        "https://github.com/EmilioEsposito",
        "https://linkedin.com/in/emilioespositousa",
        "https://resume.eesposito.com",
      ],
      knowsAbout: [
        "Artificial Intelligence",
        "AI Engineering",
        "AI Enablement",
        "Machine Learning",
        "Real Estate Investment",
        "Multi-Agent AI Systems",
        "Python",
        "TypeScript",
        "PydanticAI",
        "MCP",
      ],
      url,
      image: DEFAULT_META.image,
    }),
  ];
}

export const links: Route.LinksFunction = () => [
  generateCanonicalLink(buildUrl("/")),
];

export default function Home() {
  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      {/* Hero */}
      <section className="mb-16">
        <div className="flex flex-col-reverse items-start gap-6 sm:flex-row sm:items-center sm:gap-8">
          <div className="min-w-0 flex-1">
            <h1 className="text-4xl font-extrabold tracking-tight lg:text-5xl">
              Emilio Esposito
            </h1>
            <p className="mt-3 text-lg text-muted-foreground">
              Senior Director, AI Engineering & Enablement at LegalZoom
              <br />
              Co-founder & Managing Partner, Sernia Capital
            </p>
          </div>
          <div className="relative w-28 h-28 sm:w-32 sm:h-32 rounded-full overflow-hidden shrink-0">
            <img
              src="/images/me_emilio_headshot_2026_square.jpg"
              alt="Emilio Esposito"
              className="object-cover w-full h-full"
            />
          </div>
        </div>

        <div className="mt-8 space-y-4 text-muted-foreground">
          <P>
            I lead AI Engineering & Enablement at LegalZoom. I built the
            function from the ground up and still write production code daily,
            building AI products and helping a 200-person engineering org put
            AI tools to practical use.
          </P>
          <P>
            I also co-founded and operate{" "}
            <span className="font-medium text-foreground">Sernia Capital</span>,
            a 40-unit residential real estate portfolio. Running the business
            gives me plenty of reasons to build software. This site brings
            together some of that work, a few experiments, and tools we use
            day to day.
          </P>
        </div>

        <div className="mt-6 flex gap-5">
          {/* <a
            href="https://resume.eesposito.com"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            target="_blank"
            rel="noopener noreferrer"
          >
            <FileText className="h-4 w-4" />
            Resume
          </a> */}
          <a
            href="https://github.com/EmilioEsposito"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            target="_blank"
            rel="noopener noreferrer"
          >
            <GitHubIcon className="h-4 w-4" />
            GitHub
          </a>
          <a
            href="https://linkedin.com/in/emilioespositousa"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            target="_blank"
            rel="noopener noreferrer"
          >
            <LinkedInIcon className="h-4 w-4" />
            LinkedIn
          </a>
        </div>
      </section>

      {/* Sernia Capital */}
      <section className="mb-16">
        <H2>Software for Sernia Capital</H2>
        <P className="mt-4 text-muted-foreground">
          A few systems I've built to help run our properties, from urgent
          tenant messages to leasing follow-ups. Most are part of our daily
          operations; others are still in development.
        </P>

        <div className="mt-8 grid gap-4 md:grid-cols-2">
          <Card className="md:col-span-2">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Bot className="h-4 w-4 text-cyan-500" />
                  Sernia AI: Operations Assistant
                </CardTitle>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                An assistant for property managers, available through{" "}
                <a
                  href="https://www.quo.com/"
                  className="text-foreground underline underline-offset-4 hover:text-foreground/80 transition-colors"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Quo
                </a>{" "}
                SMS or our web app. It uses business communications and
                persistent memory to keep context, manage tasks, and help with
                follow-ups, with approval required for actions like sending
                external messages. It runs from chats, incoming events, or
                scheduled check-ins. Built with{" "}
                <a
                  href="https://ai.pydantic.dev/"
                  className="text-foreground underline underline-offset-4 hover:text-foreground/80 transition-colors"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  PydanticAI
                </a>
                .
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2 text-base">
                  <AlertTriangle className="h-4 w-4 text-amber-500" />
                  Emergency SMS Routing
                </CardTitle>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                AI checks incoming tenant messages for urgent issues and
                escalates them through Twilio calls and texts, including
                off-hours alerts configured to bypass Do Not Disturb.
              </p>
              <a
                href="https://github.com/EmilioEsposito/portfolio/blob/main/api/src/open_phone/escalate.py"
                className="mt-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                target="_blank"
                rel="noopener noreferrer"
              >
                View source <ExternalLink className="h-3 w-3" />
              </a>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Calendar className="h-4 w-4 text-blue-500" />
                  Leasing Lead Management
                </CardTitle>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Turns Zillow email threads into contacts, calendar events,
                and follow-up reminders for leasing agents, with contact
                details synced to our phone platform.
              </p>
              <a
                href="https://github.com/EmilioEsposito/portfolio/tree/main/api/src/zillow_email"
                className="mt-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
                target="_blank"
                rel="noopener noreferrer"
              >
                View source <ExternalLink className="h-3 w-3" />
              </a>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2 text-base">
                  <MessageSquare className="h-4 w-4 text-green-500" />
                  Tenant Communications
                </CardTitle>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Building-wide announcements by SMS, with role-based access
                so property managers can reach the right tenants.
              </p>
              <Link
                to="/message-tenants"
                className="mt-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                Open app <ExternalLink className="h-3 w-3" />
              </Link>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Mail className="h-4 w-4 text-purple-500" />
                  AI Leasing Auto-Replies
                </CardTitle>
                <Badge variant="outline" className="text-xs">
                  In Development
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                Drafting and testing automated replies to leasing inquiries
                using property details, listings, and agent availability,
                with applicant screening before suggesting a showing.
              </p>
              <Link
                to="/ai-email-responder"
                className="mt-3 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                Preview <ExternalLink className="h-3 w-3" />
              </Link>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* Favorite Stack */}
      <section className="mb-16">
        <H2>Tools & workflow</H2>
        <P className="mt-4 text-muted-foreground">
          What I reach for to build, ship, and maintain software.
        </P>

        <div className="mt-8 space-y-8">
          {/* Dev Environment */}
          <div>
            <h3 className="text-base font-semibold mb-3">
              Development
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-l-4 border-l-violet-500 p-4">
                <p className="font-medium">Codex</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  My first choice since June 2026 for day-to-day development,
                  from exploring a codebase to implementing features and
                  reviewing changes.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-violet-500 p-4">
                <p className="font-medium">Claude Code</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  A very close second, used in parallel with Codex. Still a
                  regular part of my workflow for features and debugging.
                  I also led its rollout across 200 engineers at LegalZoom.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-violet-500 p-4">
                <p className="font-medium">Parallel worktrees</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  A first-class part of every project. Isolated Git worktrees
                  let AI agents tackle separate tasks at the same time, each
                  with its own branch and development environment.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-violet-500 p-4">
                <p className="font-medium">Cloud maintenance agents</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Cloud-based AI agents investigate issues and handle routine
                  software maintenance, triggered by Logfire alerts or
                  scheduled runs.
                </p>
              </div>
            </div>
          </div>

          {/* AI & Agents */}
          <div>
            <h3 className="text-base font-semibold mb-3">
              AI & agents
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-l-4 border-l-cyan-500 p-4">
                <p className="font-medium">PydanticAI</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  My usual starting point for Python agents: structured
                  outputs, dependency injection, tool calling, and graphs for
                  workflows that need multiple agents.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-cyan-500 p-4">
                <p className="font-medium">FastMCP</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  For exposing tools, resources, and prompts through the
                  Model Context Protocol. I use it for MCP servers at
                  LegalZoom and in my own projects.
                </p>
              </div>
            </div>
          </div>

          {/* Frameworks */}
          <div>
            <h3 className="text-base font-semibold mb-3">
              Frameworks
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-l-4 border-l-amber-500 p-4">
                <p className="font-medium">FastAPI</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Python APIs with async support, Pydantic validation, and
                  generated documentation. A straightforward fit for the
                  backends I build.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-amber-500 p-4">
                <p className="font-medium">React Router v7</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Loaders, actions, and nested routes keep data fetching
                  close to the UI. Vite keeps the local development loop fast.
                </p>
              </div>
            </div>
          </div>

          {/* Infrastructure */}
          <div>
            <h3 className="text-base font-semibold mb-3">
              Infrastructure
            </h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div className="rounded-lg border border-l-4 border-l-green-500 p-4">
                <p className="font-medium">Railway</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Hosting for my apps and services, with monorepo support
                  and PR preview environments. The CLI and MCP tools fit
                  into my development workflow.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-green-500 p-4">
                <p className="font-medium">Logfire</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Traces and logs for Python and AI workloads, including
                  PydanticAI. I use its MCP server to investigate production
                  issues and its alerts to trigger maintenance agents.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-green-500 p-4">
                <p className="font-medium">Neon Postgres</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Postgres with database branching. Each PR preview gets an
                  isolated database, with setup and cleanup automated.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Closing */}
      <section className="mb-12">
        <H2>Selected open source</H2>
        <P className="mt-4 text-muted-foreground">
          A few things I've built and shared.
        </P>
        <div className="mt-6 space-y-6">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h3 className="font-semibold">
                <a
                  href="https://github.com/EmilioEsposito/agent-filetree-memory-mcp"
                  className="underline underline-offset-4 hover:text-muted-foreground transition-colors"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Agent Filetree Memory MCP
                  <ExternalLink className="ml-1.5 inline h-3.5 w-3.5" />
                </a>
              </h3>
              <Badge variant="outline" className="text-xs">Early alpha</Badge>
            </div>
            <p className="mt-2 text-sm text-muted-foreground">
              Persistent memory for AI agents as a Markdown file tree, backed
              by PostgreSQL with encryption at rest and version history.
              Use it through MCP or embed it in a Python service.
            </p>
          </div>
          <div>
            <h3 className="font-semibold">
              <a
                href="https://github.com/EmilioEsposito/portfolio"
                className="underline underline-offset-4 hover:text-muted-foreground transition-colors"
                target="_blank"
                rel="noopener noreferrer"
              >
                This site & Sernia tools
                <ExternalLink className="ml-1.5 inline h-3.5 w-3.5" />
              </a>
            </h3>
            <p className="mt-2 text-sm text-muted-foreground">
              Source for this site and the Sernia systems featured above:
              a React Router frontend, a FastAPI backend, and the integrations
              that connect our day-to-day operations.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
