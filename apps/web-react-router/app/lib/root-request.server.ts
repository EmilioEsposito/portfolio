import { data, type MiddlewareFunction } from "react-router";

import { isSerniaPublicRequest, rootRequestMode, SERNIA_NOT_FOUND } from "~/lib/sernia-public";

// The root route's server side, kept out of root.tsx so tests can run it
// through React Router's request handler. Clerk is passed in for the same
// reason: the tests have no Clerk keys.

/**
 * Clerk runs for the app. The Sernia public pages skip it, and anything else
 * on a Sernia host answers the Sernia 404 before Clerk would run.
 */
export function createRootMiddleware(clerk: MiddlewareFunction<Response>): MiddlewareFunction<Response> {
  return (args, next) => {
    switch (rootRequestMode(args.url)) {
      case "sernia-not-found":
        throw data(SERNIA_NOT_FOUND, { status: 404, statusText: "Not Found" });
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
    return { serniaPublic: true as const, railwayEnvironmentName: process.env.RAILWAY_ENVIRONMENT_NAME };
  }
  return appLoader();
}
