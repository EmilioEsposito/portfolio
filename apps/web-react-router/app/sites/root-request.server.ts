import { data, redirect, type MiddlewareFunction } from "react-router";

import {
  isSerniaHomeRequest,
  rootRequestMode,
  SERNIA_NOT_FOUND,
  teamHref,
  teamRedirect,
  type SerniaNotFound,
} from "~/sites/site";

// The root route's server side, kept out of root.tsx so tests can run it
// through React Router's request handler. Clerk is passed in for the same
// reason: the tests have no Clerk keys.

/** Whether `/` on the request's host is the Sernia home page. */
const homeAtRoot = (url: URL) => isSerniaHomeRequest(new URL("/", url));

/** What the root loader says about a portfolio page. */
export type PortfolioRootData = { site: "portfolio"; railwayEnvironmentName?: string };

/** What the root loader says about a Sernia public page. */
export type SerniaPublicRootData = {
  site: "sernia-public";
  /** Whether `/` on this host is the Sernia home page. */
  homeAtRoot: boolean;
  /** Where the shell's Team link goes from this host. */
  teamHref: string;
  railwayEnvironmentName?: string;
};

/** What the root loader says about a team page, before Clerk adds its state. */
export type TeamRootData = { site: "team"; railwayEnvironmentName?: string };

/**
 * Clerk runs for the team's pages and nothing else. A team path on `www.` of
 * a Sernia host first moves to the host without it, and anything a Sernia
 * host does not serve answers the Sernia 404 before any page code runs.
 */
export function createRootMiddleware(clerk: MiddlewareFunction<Response>): MiddlewareFunction<Response> {
  return (args, next) => {
    const moved = teamRedirect(args.url);
    if (moved) throw redirect(moved, 308);
    switch (rootRequestMode(args.url)) {
      case "sernia-not-found": {
        const body: SerniaNotFound = {
          reason: SERNIA_NOT_FOUND,
          homeAtRoot: homeAtRoot(args.url),
          teamHref: teamHref(args.url),
        };
        throw data(body, { status: 404, statusText: "Not Found" });
      }
      case "portfolio":
      case "sernia-public":
        return next();
      case "team":
        return clerk(args, next);
    }
  };
}

/**
 * The root loader: which site this page is, and for a team page the team's
 * loader, which adds Clerk's state.
 */
export function loadRoot<T>(
  args: { url: URL },
  teamLoader: (base: TeamRootData) => T,
): PortfolioRootData | SerniaPublicRootData | T {
  const railwayEnvironmentName = process.env.RAILWAY_ENVIRONMENT_NAME;
  switch (rootRequestMode(args.url)) {
    case "team":
      return teamLoader({ site: "team", railwayEnvironmentName });
    case "portfolio":
      return { site: "portfolio", railwayEnvironmentName };
    default:
      // The middleware answered a Sernia 404 before this could run for one.
      return {
        site: "sernia-public",
        homeAtRoot: homeAtRoot(args.url),
        teamHref: teamHref(args.url),
        railwayEnvironmentName,
      };
  }
}
