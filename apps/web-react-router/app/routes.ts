import { type RouteConfig, index, layout, prefix, route } from "@react-router/dev/routes";

// Every URL the app serves. One build answers both eesposito.com and
// serniacapital.com; app/sites/site.ts decides per request which site a
// request is for. Paths are relative to app/.

export default [
  // Both sites: these answer by host (app/sites/site.ts).
  index("sites/home.tsx"),
  route("*", "sites/not-found.tsx"),

  // Portfolio (eesposito.com): public pages, AI demos and write-ups.
  route("calendly", "sites/portfolio/routes/calendly.tsx"),
  route("multi-agent-chat", "sites/portfolio/routes/multi-agent-chat.tsx"),
  route("sernia-capital", "sites/portfolio/routes/sernia-capital.tsx"),
  route("tools-and-workflow", "sites/portfolio/routes/tools-and-workflow.tsx"),
  route("systems/emergency-routing", "sites/portfolio/routes/systems/emergency-routing.tsx"),
  route("systems/leasing-evals", "sites/portfolio/routes/systems/leasing-evals.tsx"),
  route("systems/sernia-ai", "sites/portfolio/routes/systems/sernia-ai.tsx"),
  // /systems/leasing-leads is the index of the team's tab layout, below.
  route("chat-emilio", "sites/portfolio/routes/chat-emilio.tsx"),
  route("chat-weather", "sites/portfolio/routes/chat-weather.tsx"),
  ...prefix("ai-email-responder", [
    index("sites/portfolio/routes/ai-email-responder.tsx"),
    route("architecture", "sites/portfolio/routes/ai-email-responder/architecture.tsx"),
  ]),
  route("robots.txt", "sites/portfolio/routes/robots.txt.ts"),
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

  // Sernia team (signed-in staff tools): still at these URLs until they move to
  // serniacapital.com/team.
  route("sernia-chat", "sites/team/routes/sernia-chat.tsx"),
  route("sernia-settings", "sites/team/routes/sernia-settings.tsx"),
  route("sernia-admin", "sites/team/routes/sernia-admin.tsx"),
  route("message-tenants", "sites/team/routes/message-tenants.tsx"),
  route("scheduler", "sites/team/routes/scheduler.tsx"),
  route("hitl-agent-chat", "sites/team/routes/hitl-agent-chat.tsx"),
  route("hitl-agent-workflow", "sites/team/routes/hitl-agent-workflow.tsx"),
  route("systems/leasing-leads", "sites/team/routes/leasing-leads/layout.tsx", [
    // The public Architecture tab is the portfolio's page.
    index("sites/portfolio/routes/systems/leasing-leads.tsx"),
    route("listings", "sites/team/routes/leasing-leads/listings.tsx"),
    route("settings", "sites/team/routes/leasing-leads/settings.tsx"),
    route("bookings", "sites/team/routes/leasing-leads/bookings.tsx"),
  ]),
] satisfies RouteConfig;
