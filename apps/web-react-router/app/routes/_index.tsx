import type { Route } from "./+types/_index";
import { H2, P } from "~/components/typography";
import { Link } from "react-router";
import { Badge } from "~/components/ui/badge";
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
  return [
    { title: "Emilio Esposito" },
    {
      name: "description",
      content:
        "Senior Director, AI Engineering at LegalZoom. Co-founder & Managing Partner, Sernia Capital. Building production AI systems and operating a 40-unit real estate portfolio.",
    },
  ];
}

export default function Home() {
  return (
    <div className="container mx-auto px-4 py-8 max-w-4xl">
      {/* Hero */}
      <section className="mb-16">
        <div className="flex items-center gap-8">
          <div className="flex-1">
            <h1 className="text-4xl font-extrabold tracking-tight lg:text-5xl">
              Emilio Esposito
            </h1>
            <p className="mt-3 text-lg text-muted-foreground">
              Senior Director, AI Engineering at LegalZoom
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
            I lead AI Engineering at LegalZoom, where I built the function from
            the ground up and still write production code daily: shipping
            agentic systems, deploying LLM-powered products, and driving AI
            tooling adoption (including Claude Code) across a 200-person
            engineering org.
          </P>
          <P>
            I also co-founded and operate{" "}
            <span className="font-medium text-foreground">Sernia Capital</span>,
            a 40-unit residential real estate portfolio. This site serves double
            duty: it hosts production services that power Sernia Capital's
            day-to-day operations, and it's a sandbox where I experiment with new
            technologies in my free time.
          </P>
        </div>

        <div className="mt-6 flex gap-5">
          <a
            href="https://resume.eesposito.com"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            target="_blank"
            rel="noopener noreferrer"
          >
            <FileText className="h-4 w-4" />
            Resume
          </a>
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
        <H2>Sernia Capital: AI-Powered Operations</H2>
        <P className="mt-4 text-muted-foreground">
          Every system below was designed, built, and deployed by me to solve
          real operational problems across our portfolio. These run in production
          24/7, handling everything from emergency escalation to lead
          qualification to tenant communications.
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
                Multi-modal AI assistant that operates across SMS, email, and
                our web app. It continuously reads all business communications,
                autonomously learns over time through a memory-based feedback
                loop, and can take actions (sending messages, managing tasks,
                scheduling jobs) with human-in-the-loop approval. Triggered
                both by incoming messages and on a scheduler so it proactively
                follows up with property managers on project status. Built with
                PydanticAI.
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
                Agentic AI monitors every inbound tenant message in real-time.
                Urgent issues trigger automated call/text escalations via Twilio
                that bypass Do Not Disturb during off-hours.
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
                  Intelligent Lead Management
                </CardTitle>
                <Badge variant="secondary" className="text-xs">
                  Production
                </Badge>
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">
                AI monitors Zillow email threads, extracting contacts, creating
                calendar events, syncing to our phone platform, and triggering
                follow-up reminders for leasing agents.
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
                Bulk SMS messaging for building-wide announcements, built to work
                around platform limitations with role-based access control.
              </p>
              <Link
                to="/tenant-mass-messaging"
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
                Agentic AI auto-responds to inbound leasing inquiries with full
                context on properties, listings, and agent availability. Screens
                applicants before proposing meeting times.
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
        <H2>Favorite Stack</H2>
        <P className="mt-4 text-muted-foreground">
          Tools I reach for first when starting something new.
        </P>

        <div className="mt-8 space-y-8">
          {/* AI & Agents */}
          <div>
            <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-3">
              AI & Agents
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-l-4 border-l-cyan-500 p-4">
                <p className="font-medium">PydanticAI</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  The right level of abstraction for building agents:
                  structured outputs, dependency injection, and tool calling
                  without fighting the framework. The Graph Beta API is
                  excellent for multi-agent workflows.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-cyan-500 p-4">
                <p className="font-medium">FastMCP</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  The FastAPI of MCP servers. Makes it trivial to expose tools,
                  resources, and prompts over the Model Context Protocol. I've
                  built MCP servers for LegalZoom and use them heavily in my
                  own workflows.
                </p>
              </div>
            </div>
          </div>

          {/* Dev Environment */}
          <div>
            <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-3">
              Dev Environment
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-l-4 border-l-violet-500 p-4">
                <p className="font-medium">Claude Code</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  My go-to for tackling larger features end-to-end. Changed
                  how I write software. I use it for everything from
                  greenfield projects to navigating massive legacy codebases.
                  Led its rollout across 200 engineers at LegalZoom.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-violet-500 p-4">
                <p className="font-medium">Cursor</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  My daily driver IDE for when I want to be in the driver's
                  seat with a bit of AI assistance (tab autocomplete, inline
                  suggestions) to keep the flow smooth without giving up
                  control.
                </p>
              </div>
            </div>
          </div>

          {/* Frameworks */}
          <div>
            <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-3">
              Frameworks
            </h3>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border border-l-4 border-l-amber-500 p-4">
                <p className="font-medium">FastAPI</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Type-safe Python with async support and auto-generated docs.
                  Pairs perfectly with Pydantic models and makes building AI
                  backends genuinely enjoyable.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-amber-500 p-4">
                <p className="font-medium">React Router v7</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Remix rebranded. Loaders, actions, nested routes, and the
                  Vite bundler makes hot-reloading noticeably faster than
                  Next.js.
                </p>
              </div>
            </div>
          </div>

          {/* Infrastructure */}
          <div>
            <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-3">
              Infrastructure
            </h3>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div className="rounded-lg border border-l-4 border-l-green-500 p-4">
                <p className="font-medium">Railway</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Deploy anything without thinking about infrastructure.
                  Monorepo support, PR preview environments, and a CLI that
                  actually works. Their MCP server makes deploying and
                  debugging from Claude Code seamless.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-green-500 p-4">
                <p className="font-medium">Logfire</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Observability from the Pydantic team, built for Python and
                  AI workloads. Native PydanticAI tracing out of the box. Their
                  MCP server lets me query traces and debug production issues
                  without leaving the terminal.
                </p>
              </div>
              <div className="rounded-lg border border-l-4 border-l-green-500 p-4">
                <p className="font-medium">Neon Postgres</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Serverless Postgres with branching. I spin up isolated
                  database branches for every PR environment. Zero config,
                  zero cleanup.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Closing */}
      <section className="mb-12">
        <P className="text-muted-foreground">
          Full source for this site is{" "}
          <a
            href="https://github.com/EmilioEsposito/portfolio"
            className="text-foreground underline underline-offset-4 hover:text-foreground/80 transition-colors"
            target="_blank"
            rel="noopener noreferrer"
          >
            open on GitHub
          </a>
          .
        </P>
      </section>
    </div>
  );
}
