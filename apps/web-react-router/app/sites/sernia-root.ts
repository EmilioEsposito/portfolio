import { isRouteErrorResponse } from "react-router";

import { SERNIA_NOT_FOUND } from "~/sites/site";

/** What the server says about a request it served as one of Sernia Capital's public pages. */
export type SerniaRoot = {
  /** Whether `/` on this host is the Sernia home page, so the wordmark can link to it. */
  homeAtRoot: boolean;
};

/**
 * Whether a page is one of Sernia Capital's: the root middleware threw the
 * Sernia 404, or the root loader served a Sernia public request. Null for
 * every other page.
 */
export function serniaRoot(error: unknown, rootData: unknown): SerniaRoot | null {
  if (isRouteErrorResponse(error) && (error.data as { reason?: unknown } | null)?.reason === SERNIA_NOT_FOUND) {
    return { homeAtRoot: (error.data as SerniaRoot).homeAtRoot === true };
  }
  if (typeof rootData === "object" && rootData !== null && "serniaPublic" in rootData) {
    return { homeAtRoot: (rootData as Partial<SerniaRoot>).homeAtRoot === true };
  }
  return null;
}
