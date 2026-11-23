import { type RouteConfig, index, layout, prefix, route } from "@react-router/dev/routes";

// Every URL the app serves. One build answers both eesposito.com and
// serniacapital.com; app/sites/site.ts decides per request which site a
// request is for, and only the team's pages run Clerk. Paths are relative to
// app/.

export default [
  // Both sites: these answer by host (app/sites/site.ts).
  index("sites/home.tsx"),
  route("*", "sites/not-found.tsx"),
  route("robots.txt", "sites/robots.txt.ts"),

  // Portfolio (eesposito.com): public pages, AI demos and write-ups.
  route("calendly", "sites/portfolio/routes/calendly.tsx"),
  route("multi-agent-chat", "sites/portfolio/routes/multi-agent-chat.tsx"),
  route("sernia-capital", "sites/portfolio/routes/sernia-capital.tsx"),
  route("tools-and-workflow", "sites/portfolio/routes/tools-and-workflow.tsx"),
  route("systems/emergency-routing", "sites/portfolio/routes/systems/emergency-routing.tsx"),
  route("systems/leasing-evals", "sites/portfolio/routes/systems/leasing-evals.tsx"),
  route("systems/sernia-ai", "sites/portfolio/routes/systems/sernia-ai.tsx"),
  route("systems/leasing-leads", "sites/portfolio/routes/systems/leasing-leads.tsx"),
  route("chat-emilio", "sites/portfolio/routes/chat-emilio.tsx"),
  route("chat-weather", "sites/portfolio/routes/chat-weather.tsx"),
  ...prefix("ai-email-responder", [
    index("sites/portfolio/routes/ai-email-responder.tsx"),
    route("architecture", "sites/portfolio/routes/ai-email-responder/architecture.tsx"),
  ]),
  route("sitemap.xml", "sites/portfolio/routes/sitemap.xml.ts"),

  // Sernia public (serniacapital.com): no sign-in and no Clerk. Each page sits
  // at a path isSerniaPublicPath() accepts, under this layout.
  layout("sites/sernia-public/routes/layout.tsx", [
    route("privacy", "sites/sernia-public/routes/privacy.tsx"),
    route("sms-terms", "sites/sernia-public/routes/sms-terms.tsx"),
    ...prefix("showings", [
      index("sites/sernia-public/routes/showings.tsx"),
      route(":unit", "sites/sernia-public/routes/showings/unit.tsx"),
      route("cancel/:token", "sites/sernia-public/routes/showings/cancel.tsx"),
    ]),
  ]),

  // Sernia team: the signed-in staff pages, at serniacapital.com/team, and at
  // /team on any host outside production (local, dev.eesposito.com, PR
  // previews). The only pages that run Clerk; the layout's guard lets in a
  // verified @serniacapital.com account.
  ...prefix("team", [
    layout("sites/team/routes/layout.tsx", [
      index("sites/team/routes/home.tsx"),
      route("chat", "sites/team/routes/chat.tsx"),
      route("settings", "sites/team/routes/settings.tsx"),
      route("admin", "sites/team/routes/admin.tsx"),
      route("tenants", "sites/team/routes/tenants.tsx"),
      route("scheduler", "sites/team/routes/scheduler.tsx"),
      route("hitl-chat", "sites/team/routes/hitl-chat.tsx"),
      route("hitl-queue", "sites/team/routes/hitl-queue.tsx"),
      route("leads", "sites/team/routes/leads/layout.tsx", [
        index("sites/team/routes/leads/overview.tsx"),
        route("listings", "sites/team/routes/leads/listings.tsx"),
        route("settings", "sites/team/routes/leads/settings.tsx"),
        route("bookings", "sites/team/routes/leads/bookings.tsx"),
      ]),
    ]),
  ]),
] satisfies RouteConfig;
