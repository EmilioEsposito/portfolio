import type { Route } from "./+types/_index";
import { Link } from "react-router";
import {
  SITE_OWNER,
  DEFAULT_META,
  buildUrl,
  generateOgMeta,
  generateJsonLd,
  generateCanonicalLink,
} from "~/lib/seo";
export function meta(_args: Route.MetaArgs) {
  const url = buildUrl("/");
  return [
    { title: DEFAULT_META.title },
    { name: "description", content: DEFAULT_META.description },
    ...generateOgMeta({
      title: DEFAULT_META.title,
      description: DEFAULT_META.description,
      url,
      type: "profile",
    }),
    generateJsonLd({
      "@context": "https://schema.org",
      "@graph": [
        { "@type": "WebSite", "@id": `${url}#website`, url, name: SITE_OWNER },
        {
          "@type": "Organization",
          "@id": `${url}#sernia-ventures`,
          name: "Sernia Ventures",
          url: "https://serniaventures.com/",
          description:
            "Independent AI-first software platforms that grew out of Sernia Capital's operational needs.",
        },
        {
          "@type": "ProfilePage",
          "@id": url,
          url,
          name: DEFAULT_META.title,
          description: DEFAULT_META.description,
          mainEntity: { "@id": `${url}#person` },
          inLanguage: "en-US",
          isPartOf: { "@id": `${url}#website` },
          hasPart: [
            { "@type": "WebPage", "@id": buildUrl("/sernia-capital") },
            { "@type": "WebPage", "@id": buildUrl("/tools-and-workflow") },
          ],
        },
        {
          "@type": "Person",
          "@id": `${url}#person`,
          name: SITE_OWNER,
          description: DEFAULT_META.description,
          jobTitle: "Senior Director, AI Engineering & Enablement",
          worksFor: {
            "@type": "Organization",
            name: "LegalZoom",
            url: "https://www.legalzoom.com/",
          },
          affiliation: [
            { "@id": `${url}#sernia-capital` },
            { "@id": `${url}#sernia-ventures` },
          ],
          sameAs: [
            "https://github.com/EmilioEsposito",
            "https://linkedin.com/in/emilioespositousa",
            "https://resume.eesposito.com",
          ],
          knowsAbout: [
            "AI engineering leadership",
            "AI developer enablement",
            "Production AI systems",
            "Multi-agent AI systems",
            "Software architecture",
            "Python",
            "TypeScript",
            "PydanticAI",
            "Model Context Protocol (MCP)",
            "Real estate operations",
          ],
          url,
          image: DEFAULT_META.image,
          mainEntityOfPage: { "@id": url },
        },
        {
          "@type": "Organization",
          "@id": `${url}#sernia-capital`,
          name: "Sernia Capital",
          founder: { "@id": `${url}#person` },
        },
        {
          "@type": "SoftwareSourceCode",
          name: "Agent Filetree Memory MCP",
          description:
            "Persistent agent memory as a Markdown file tree, backed by PostgreSQL with encryption at rest and version history.",
          codeRepository:
            "https://github.com/EmilioEsposito/agent-filetree-memory-mcp",
          url: "https://github.com/EmilioEsposito/agent-filetree-memory-mcp",
          author: { "@id": `${url}#person` },
        },
        {
          "@type": "SoftwareSourceCode",
          name: "This site & Sernia tools",
          description:
            "A React Router frontend, FastAPI backend, and integrations for Sernia Capital's real estate operations.",
          codeRepository: "https://github.com/EmilioEsposito/portfolio",
          url: "https://github.com/EmilioEsposito/portfolio",
          author: { "@id": `${url}#person` },
        },
      ],
    }),
  ];
}

export const links: Route.LinksFunction = () => [
  generateCanonicalLink(buildUrl("/")),
];

const textLink =
  "inline-flex items-center gap-2 rounded-sm text-sm font-medium underline underline-offset-4 decoration-border hover:decoration-foreground focus-visible:outline-2 focus-visible:outline-offset-4";

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-5xl px-6 pb-12 pt-12 sm:px-10 sm:pt-20">
      <section aria-labelledby="profile-heading" className="pb-14 sm:pb-20">
        <div className="flex flex-col-reverse justify-between gap-8 sm:flex-row sm:items-center">
          <div className="max-w-2xl">
            <h1
              id="profile-heading"
              className="text-4xl font-semibold tracking-tight sm:text-6xl"
            >
              Emilio Esposito
            </h1>
            <p className="mt-5 text-xl leading-relaxed text-muted-foreground sm:text-2xl">
              AI engineering leader.
              <br />
              Hands-on builder.
            </p>
          </div>
          <img
            src="/images/me_emilio_headshot_2026_square.jpg"
            alt="Emilio Esposito"
            width={160}
            height={160}
            className="h-28 w-28 shrink-0 rounded-2xl object-cover sm:h-40 sm:w-40"
          />
        </div>
        <p className="mt-8 max-w-2xl text-base leading-7 text-muted-foreground sm:text-lg sm:leading-8">
          I build AI products, help engineering teams put AI to work, and
          operate businesses that give me a direct stake in the software I ship.
        </p>
        <div className="mt-7 flex flex-wrap gap-6">
          <a
            href="https://linkedin.com/in/emilioespositousa"
            className={textLink}
          >
            LinkedIn
          </a>
          <a href="https://github.com/EmilioEsposito" className={textLink}>
            GitHub
          </a>
          <Link to="/calendly" className={textLink}>
            Get in touch
          </Link>
        </div>
      </section>

      <section
        id="leadership"
        aria-labelledby="leadership-heading"
        className="grid scroll-mt-8 gap-5 border-t py-12 sm:grid-cols-[190px_1fr] sm:gap-10 sm:py-14"
      >
        <div>
          <h2 id="leadership-heading" className="text-lg font-semibold">
            AI leadership
          </h2>
          <p className="mt-2 text-sm text-muted-foreground">LegalZoom</p>
        </div>
        <div>
          <h3 className="text-2xl font-medium tracking-tight">
            Senior Director,
            <br className="hidden sm:block" /> AI Engineering &amp; Enablement
          </h3>
          <p className="mt-5 leading-7 text-muted-foreground">
            I built the AI Engineering &amp; Enablement function from the ground
            up. My work combines engineering leadership with daily production
            coding: building AI products and helping a 200-person engineering
            organization adopt AI tools.
          </p>
          <p className="mt-4 leading-7 text-muted-foreground">
            That includes rolling out Codex and Claude Code across the
            engineering organization, introducing open-source models and agent
            harnesses, and building MCP servers that connect AI tools to the
            systems teams use.
          </p>
          <Link to="/tools-and-workflow" className={textLink + " mt-6"}>
            My tools &amp; development workflow
          </Link>
        </div>
      </section>

      <section
        id="sernia-systems"
        aria-labelledby="capital-heading"
        className="grid scroll-mt-8 gap-5 border-t py-12 sm:grid-cols-[190px_1fr] sm:gap-10 sm:py-14"
      >
        <div>
          <h2 id="capital-heading" className="text-lg font-semibold">
            Sernia Capital
          </h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            Co-founder &amp;
            <br />
            Managing Partner
          </p>
        </div>
        <div>
          <h3 className="text-2xl font-medium tracking-tight">
            Operating a business.
            <br />
            Building the systems behind it.
          </h3>
          <p className="mt-5 leading-7 text-muted-foreground">
            I co-founded and operate a 40-unit residential real estate
            portfolio. I also build the software we use to manage
            communications, respond to urgent issues, and follow up with
            prospective tenants.
          </p>
          <div className="mt-7 rounded-xl bg-muted/60 p-6">
            <p className="text-sm text-muted-foreground">In daily operations</p>
            <h4 className="mt-2 text-lg font-semibold">
              SerniaAI operations agent
            </h4>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              An agent that works across SMS and our web app, using business
              communications and persistent memory to manage tasks and
              follow-ups. External actions such as sending messages require
              approval.
            </p>
            <Link to="/systems/sernia-ai" className={textLink + " mt-4"}>
              Inside the operations agent
            </Link>
          </div>
          <div className="mt-6 grid gap-6 sm:grid-cols-2">
            <div>
              <h4 className="font-medium">
                <Link
                  to="/systems/emergency-routing"
                  className="underline decoration-border underline-offset-4 hover:decoration-foreground"
                >
                  Emergency routing
                </Link>
              </h4>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                AI assesses urgent tenant messages; application rules route
                qualifying alerts.
              </p>
            </div>
            <div>
              <h4 className="font-medium">
                <Link
                  to="/systems/leasing-leads"
                  className="underline decoration-border underline-offset-4 hover:decoration-foreground"
                >
                  Leasing follow-ups
                </Link>
              </h4>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                Email review drives contact updates, calendar events, and
                missing-reply alerts.
              </p>
            </div>
          </div>
          <div className="mt-7 flex flex-wrap gap-x-6 gap-y-3">
            <Link to="/sernia-capital" className={textLink}>
              All Sernia Capital systems
            </Link>
            <a
              href="https://github.com/EmilioEsposito/portfolio"
              className={textLink}
            >
              View source
            </a>
          </div>
        </div>
      </section>

      <section
        id="sernia-ventures"
        aria-labelledby="ventures-heading"
        className="grid scroll-mt-8 gap-5 border-t py-12 sm:grid-cols-[190px_1fr] sm:gap-10 sm:py-14"
      >
        <div>
          <h2 id="ventures-heading" className="text-lg font-semibold">
            Sernia Ventures
          </h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Independent software
          </p>
        </div>
        <div>
          <h3 className="text-2xl font-medium tracking-tight">
            Products I build and operate.
          </h3>
          <p className="mt-5 leading-7 text-muted-foreground">
            Through Sernia Ventures, I build AI-first software platforms for
            other teams and businesses to use. These products grew out of
            operational needs at Sernia Capital, which became their first
            customer.
          </p>
          <div className="mt-7 divide-y">
            <article className="pb-6">
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h4 className="text-lg font-semibold">
                  <a
                    href="https://markdownmem.com/"
                    className="underline decoration-border underline-offset-4 hover:decoration-foreground"
                  >
                    MarkdownMem
                  </a>
                </h4>
              </div>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                Portable Markdown memory for AI agents.
              </p>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                Built on my open-source{" "}
                <a
                  href="https://github.com/EmilioEsposito/agent-filetree-memory-mcp"
                  className="text-foreground underline underline-offset-4"
                >
                  Agent Filetree Memory MCP
                </a>
                : persistent, versioned memory backed by PostgreSQL, available
                through MCP or Python.
              </p>
            </article>
            <article className="py-6">
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h4 className="text-lg font-semibold">
                  <a
                    href="https://docjig.com/"
                    className="underline decoration-border underline-offset-4 hover:decoration-foreground"
                  >
                    Docjig
                  </a>
                </h4>
                <span className="text-xs text-muted-foreground">
                  Early access
                </span>
              </div>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                Turn existing documents into reusable templates.
              </p>
            </article>
            <article className="pt-6">
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h4 className="text-lg font-semibold">
                  <a
                    href="https://rentium.app/"
                    className="underline decoration-border underline-offset-4 hover:decoration-foreground"
                  >
                    Rentium
                  </a>
                </h4>
                <span className="text-xs text-muted-foreground">
                  Private preview
                </span>
              </div>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                Software for rental property management.
              </p>
            </article>
          </div>
          <a href="https://serniaventures.com/" className={textLink + " mt-8"}>
            Visit Sernia Ventures
          </a>
        </div>
      </section>
      <footer className="flex flex-wrap items-center justify-between gap-4 border-t pt-8 text-sm text-muted-foreground">
        <p>Emilio Esposito</p>
        <Link to="/multi-agent-chat" className="underline underline-offset-4">
          Explore the AI demos
        </Link>
      </footer>
    </main>
  );
}
