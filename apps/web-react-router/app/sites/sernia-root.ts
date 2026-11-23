import { isRouteErrorResponse } from "react-router";

import { SERNIA_NOT_FOUND } from "~/sites/site";

/** What the server says about a request it served as one of Sernia Capital's public pages. */
export type SerniaRoot = {
  /** Whether `/` on this host is the Sernia home page, so the wordmark can link to it. */
  homeAtRoot: boolean;
  /** Where the shell's Team link goes from this host. */
  teamHref: string;
};

type SerniaRootFields = { homeAtRoot?: unknown; teamHref?: unknown };

function read(fields: SerniaRootFields): SerniaRoot {
  return {
    homeAtRoot: fields.homeAtRoot === true,
    teamHref: typeof fields.teamHref === "string" ? fields.teamHref : "/team",
  };
}

/**
 * Whether a page is one of Sernia Capital's public pages: the root middleware
 * threw the Sernia 404, or the root loader served a Sernia public request.
 * Null for every other page, the team's included.
 */
export function serniaRoot(error: unknown, rootData: unknown): SerniaRoot | null {
  if (isRouteErrorResponse(error) && (error.data as { reason?: unknown } | null)?.reason === SERNIA_NOT_FOUND) {
    return read(error.data as SerniaRootFields);
  }
  if (rootSite(rootData) === "sernia-public") return read(rootData as SerniaRootFields);
  return null;
}

/** The site the root loader named, or null when it did not run. */
export function rootSite(rootData: unknown): "portfolio" | "sernia-public" | "team" | null {
  if (typeof rootData !== "object" || rootData === null) return null;
  const site = (rootData as { site?: unknown }).site;
  return site === "portfolio" || site === "sernia-public" || site === "team" ? site : null;
}
