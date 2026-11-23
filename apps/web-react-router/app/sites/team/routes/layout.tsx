import { useEffect } from "react";
import { ClerkProvider } from "@clerk/react-router";
import { data, Outlet, useMatches, useNavigation, useRouteLoaderData } from "react-router";

import type { Route } from "./+types/layout";
import { EnvBanner } from "~/components/env-banner";
import { SidebarInset, SidebarProvider } from "~/components/ui/sidebar";
import { retireRootServiceWorkerOnce } from "~/lib/retire-root-service-worker";
import { TeamDeviceBanner, TeamDeviceProvider } from "~/sites/team/components/team-device";
import { TeamGuard } from "~/sites/team/components/team-guard";
import { TeamSidebar, TeamTopBar, type TeamLayoutData } from "~/sites/team/components/team-nav";
import { keepInstallPrompt } from "~/sites/team/lib/install-prompt";
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

/** The root loader's data on a team page: what Clerk's provider reads, plus the environment name. */
type ClerkRootData = React.ComponentProps<typeof ClerkProvider>["loaderData"] & { railwayEnvironmentName?: string };

/** Team pages that bring their own sidebar (the conversation sidebar) set this `handle`. */
export type TeamPageHandle = { ownSidebar?: boolean };

/** A line across the top from the moment a link is clicked until its page opens. */
function OpeningLine() {
  if (useNavigation().state === "idle") return null;
  return (
    <div
      role="progressbar"
      aria-label="Opening the page"
      className="sernia-brand sernia-opening fixed inset-x-0 top-0 z-50 h-[3px] origin-left bg-sernia-sky"
    />
  );
}

export default function TeamLayout() {
  // The root loader ran Clerk for this request and carries its state.
  const root = useRouteLoaderData("root") as ClerkRootData | undefined;
  const ownSidebar = useMatches().some((match) => (match.handle as TeamPageHandle | undefined)?.ownSidebar);
  useEffect(retireRootServiceWorkerOnce, []);
  // The browser may offer its install prompt before anyone has signed in.
  useEffect(() => keepInstallPrompt(), []);
  const banner = <EnvBanner railwayEnvironmentName={root?.railwayEnvironmentName} />;

  // The only Clerk provider on the site, in the team's own layout so that no
  // other page downloads Clerk's code. Signing out keeps the person on /team.
  return (
    <ClerkProvider loaderData={root} afterSignOutUrl="/team">
      <TeamPages banner={banner} ownSidebar={ownSidebar} />
    </ClerkProvider>
  );
}

function TeamPages({ banner, ownSidebar }: { banner: React.ReactNode; ownSidebar: boolean }) {
  return (
    <TeamGuard banner={banner}>
      {/* One notification subscription and one set of reminders for every team page. */}
      <TeamDeviceProvider>
        <OpeningLine />
        {ownSidebar ? (
          <>
            {banner}
            {/* A page with its own sidebar fills the screen, so it places TeamDeviceBanner
                itself, under its header (the chat, its settings, All conversations);
                test/lib/team-pages.test.ts checks that each does. */}
            <Outlet />
          </>
        ) : (
          <SidebarProvider>
            <TeamSidebar />
            <SidebarInset className="min-w-0 overflow-x-clip">
              {banner}
              <TeamTopBar />
              <TeamDeviceBanner />
              <Outlet />
            </SidebarInset>
          </SidebarProvider>
        )}
      </TeamDeviceProvider>
    </TeamGuard>
  );
}
