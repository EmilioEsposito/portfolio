import type { Route } from "./+types/robots.txt";
import { SITE_URL } from "~/sites/portfolio/lib/seo";
import { isSerniaHost } from "~/sites/site";

// robots.txt answers by host. A Sernia host keeps crawlers out of the team's
// pages; the portfolio's allows everything and names its sitemap.

const SERNIA_ROBOTS = `# Robots.txt for serniacapital.com

User-agent: *
Disallow: /team
`;

const PORTFOLIO_ROBOTS = `# Robots.txt for ${SITE_URL}

User-agent: *
Allow: /

# AI Crawlers - explicitly allowed
User-agent: GPTBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: Applebot-Extended
Allow: /

Sitemap: ${SITE_URL}/sitemap.xml
`;

export function loader({ url }: Route.LoaderArgs) {
  return new Response(isSerniaHost(url.host) ? SERNIA_ROBOTS : PORTFOLIO_ROBOTS, {
    headers: {
      "Content-Type": "text/plain",
      "Cache-Control": "public, max-age=86400",
    },
  });
}
