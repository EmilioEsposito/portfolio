import { P } from "~/components/typography";
import { Link } from "react-router";
import { buildUrl, generateCanonicalLink, generatePageMeta } from "~/lib/seo";
export const meta = () =>
  generatePageMeta({
    title: "Tools & workflow",
    description:
      "How Emilio Esposito develops, ships, and maintains software with AI agents, isolated worktrees, and production observability.",
    path: "/tools-and-workflow",
  });
export const links = () => [
  generateCanonicalLink(buildUrl("/tools-and-workflow")),
];
export default function ToolsAndWorkflow() {
  return (
    <main className="mx-auto max-w-4xl px-6 py-12">
      <Link to="/" className="text-sm underline underline-offset-4">
        Back to profile
      </Link>
      <h1 className="mt-8 text-4xl font-semibold tracking-tight">
        Tools & workflow
      </h1>{" "}
      {/* Favorite Stack */}
      <section className="mb-16">
        <P className="mt-4 text-muted-foreground">
          What I reach for to build, ship, and maintain software.
        </P>

        <div className="mt-8 space-y-8">
          {/* Dev Environment */}
          <div>
            <h2 className="text-base font-semibold mb-3">Development</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border  p-4">
                <p className="font-medium">Codex</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  My first choice since June 2026 for day-to-day development,
                  from exploring a codebase to implementing features and
                  reviewing changes.
                </p>
              </div>
              <div className="rounded-lg border  p-4">
                <p className="font-medium">Claude Code</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  A very close second, used in parallel with Codex. Still a
                  regular part of my workflow for features and debugging. I also
                  led its rollout across 200 engineers at LegalZoom.
                </p>
              </div>
              <div className="rounded-lg border  p-4">
                <p className="font-medium">Parallel worktrees</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  A first-class part of every project. Isolated Git worktrees
                  let AI agents tackle separate tasks at the same time, each
                  with its own branch and development environment.
                </p>
              </div>
              <div className="rounded-lg border  p-4">
                <p className="font-medium">Cloud maintenance agents</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Cloud-based AI agents investigate issues and handle routine
                  software maintenance, triggered by Logfire alerts or scheduled
                  runs.
                </p>
              </div>
            </div>
          </div>

          {/* AI & Agents */}
          <div>
            <h2 className="text-base font-semibold mb-3">AI & agents</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border  p-4 sm:col-span-2">
                <p className="font-medium">OpenRouter</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  My API inference layer for frontier and open-source models. It
                  makes it easy to choose the right model for each job and
                  balance quality with cost, with governance and guardrails for
                  agent spending, model access, and data privacy.
                </p>
              </div>
              <div className="rounded-lg border  p-4">
                <p className="font-medium">PydanticAI</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  My usual starting point for Python agents: structured outputs,
                  dependency injection, tool calling, and graphs for workflows
                  that need multiple agents.
                </p>
              </div>
              <div className="rounded-lg border  p-4">
                <p className="font-medium">FastMCP</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  For exposing tools, resources, and prompts through the Model
                  Context Protocol. I use it for MCP servers at LegalZoom and in
                  my own projects.
                </p>
              </div>
            </div>
          </div>

          {/* Frameworks */}
          <div>
            <h2 className="text-base font-semibold mb-3">Frameworks</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="rounded-lg border  p-4">
                <p className="font-medium">FastAPI</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Python APIs with async support, Pydantic validation, and
                  generated documentation. A straightforward fit for the
                  backends I build.
                </p>
              </div>
              <div className="rounded-lg border  p-4">
                <p className="font-medium">React Router v8</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Loaders, actions, and nested routes keep data fetching close
                  to the UI. Vite keeps the local development loop fast.
                </p>
              </div>
            </div>
          </div>

          {/* Infrastructure */}
          <div>
            <h2 className="text-base font-semibold mb-3">Infrastructure</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div className="rounded-lg border  p-4">
                <p className="font-medium">Railway</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Hosting for my apps and services, with monorepo support and PR
                  preview environments. The CLI and MCP tools fit into my
                  development workflow.
                </p>
              </div>
              <div className="rounded-lg border  p-4">
                <p className="font-medium">Logfire</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Traces and logs for Python and AI workloads, including
                  PydanticAI. I use its MCP server to investigate production
                  issues and its alerts to trigger maintenance agents.
                </p>
              </div>
              <div className="rounded-lg border  p-4">
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
    </main>
  );
}
