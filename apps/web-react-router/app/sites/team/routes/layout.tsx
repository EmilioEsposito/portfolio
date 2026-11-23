import { useEffect } from "react";
import { data, Outlet, useMatches, useRouteLoaderData } from "react-router";

import type { Route } from "./+types/layout";
import { EnvBanner } from "~/components/env-banner";
import { SidebarInset, SidebarProvider } from "~/components/ui/sidebar";
import { retireRootServiceWorkerOnce } from "~/lib/retire-root-service-worker";
import { TeamGuard } from "~/sites/team/components/team-guard";
import { TeamSidebar, TeamTopBar, type TeamLayoutData } from "~/sites/team/components/team-nav";
import { isSerniaHomeRequest, isSerniaHost, rootRequestMode } from "~/sites/site";

// The layout of every team page (/team and /team/*). Each page under it sits
// at a team path, so the root runs Clerk for it; test/lib/site-rule.test.ts
// checks app/routes.ts.

// In production a team path on a host other than Sernia's is the portfolio's
// 404, as an unknown URL is. The root has served it as a portfolio page, so
// this answers the 404 before any team code runs.
export const middleware: Route.MiddlewareFunction[] = [
  (args, next) => {
    if (rootRequestMode(args.url) !== "team") {
      throw data(null, { status: 404, statusText: "Not Found" });
    }
    return next();
  },
];

/**
 * Where the public site is from this host, for the navigation. Its presence
 * also makes entering a team page from elsewhere always ask the server, so
 * the middleware above decides there too.
 */
export function loader({ url }: Route.LoaderArgs): TeamLayoutData {
  if (isSerniaHomeRequest(new URL("/", url))) return { publicSiteHref: "/" };
  // A Sernia host whose public pages are switched off has none to link to.
  return { publicSiteHref: isSerniaHost(url.host) ? null : "https://serniacapital.com/" };
}

/** Team pages that bring their own sidebar (the conversation sidebar) set this `handle`. */
export type TeamPageHandle = { ownSidebar?: boolean };

export default function TeamLayout() {
  const root = useRouteLoaderData("root") as { railwayEnvironmentName?: string } | undefined;
  const ownSidebar = useMatches().some((match) => (match.handle as TeamPageHandle | undefined)?.ownSidebar);
  useEffect(retireRootServiceWorkerOnce, []);
  const banner = <EnvBanner railwayEnvironmentName={root?.railwayEnvironmentName} />;

  return (
    <TeamGuard banner={banner}>
      {ownSidebar ? (
        <>
          {banner}
          <Outlet />
        </>
      ) : (
        <SidebarProvider>
          <TeamSidebar />
          <SidebarInset className="min-w-0 overflow-x-clip">
            {banner}
            <TeamTopBar />
            <Outlet />
          </SidebarInset>
        </SidebarProvider>
      )}
    </TeamGuard>
  );
}
