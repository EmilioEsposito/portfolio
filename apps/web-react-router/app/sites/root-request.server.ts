import { data, type MiddlewareFunction } from "react-router";

import {
  isSerniaHomeRequest,
  isSerniaPublicRequest,
  rootRequestMode,
  SERNIA_NOT_FOUND,
  type SerniaNotFound,
} from "~/sites/site";

// The root route's server side, kept out of root.tsx so tests can run it
// through React Router's request handler. Clerk is passed in for the same
// reason: the tests have no Clerk keys.

/** Whether `/` on the request's host is the Sernia home page. */
const homeAtRoot = (url: URL) => isSerniaHomeRequest(new URL("/", url));

/**
 * Clerk runs for the app. The Sernia public pages skip it, and anything else
 * on a Sernia host answers the Sernia 404 before Clerk would run.
 */
export function createRootMiddleware(clerk: MiddlewareFunction<Response>): MiddlewareFunction<Response> {
  return (args, next) => {
    switch (rootRequestMode(args.url)) {
      case "sernia-not-found": {
        const body: SerniaNotFound = { reason: SERNIA_NOT_FOUND, homeAtRoot: homeAtRoot(args.url) };
        throw data(body, { status: 404, statusText: "Not Found" });
      }
      case "sernia-public":
        return next();
      case "app":
        return clerk(args, next);
    }
  };
}

/** No Clerk state for a Sernia public request; the app's loader otherwise. */
export function loadRoot<T>(args: { url: URL }, appLoader: () => T) {
  if (isSerniaPublicRequest(args.url)) {
    // The middleware skipped Clerk, so rootAuthLoader cannot run here.
    return {
      serniaPublic: true as const,
      homeAtRoot: homeAtRoot(args.url),
      railwayEnvironmentName: process.env.RAILWAY_ENVIRONMENT_NAME,
    };
  }
  return appLoader();
}
