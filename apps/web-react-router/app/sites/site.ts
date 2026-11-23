/**
 * Which site answers a request. One build serves three:
 *
 * - The portfolio (eesposito.com): public, with no sign-in and no Clerk.
 * - Sernia Capital's public pages (serniacapital.com): /showings,
 *   /showings/*, /privacy and /sms-terms on any host, and the home page at /
 *   on a Sernia host only. No sign-in and no Clerk. They are switched off in
 *   production until SERNIA_PUBLIC_PAGES_ENABLED is "true"; see
 *   serniaPublicPagesEnabled().
 * - The Sernia team's pages: /team and /team/*. The only signed-in area, and
 *   the only requests that run Clerk. Served on a Sernia host always, and on
 *   any other host only outside production, so local runs, the development
 *   environment (dev.eesposito.com) and PR previews have them too. In
 *   production a team path on another host is the portfolio's 404.
 *
 * The root route, the site layouts, server.js and the Vite dev proxy all
 * decide from this module. server.js imports it directly, relying on Node's
 * built-in TypeScript type stripping, so it must keep no imports and use only
 * syntax that can be erased (no enums or namespaces).
 */

/** Sernia Capital's hosts. SERNIA_PUBLIC_HOSTS replaces this list. */
export const DEFAULT_SERNIA_PUBLIC_HOSTS = [
  "serniacapital.com",
  "www.serniacapital.com",
  "serniacapital.localhost",
] as const;

/** The public booking API, the only API the Sernia public pages call. */
export const SERNIA_PUBLIC_API_PREFIX = "/api/showing-bookings/public/";

/**
 * The API paths the team's pages call, which the /api proxy forwards on a
 * Sernia host as well. An entry ending in "/" covers every path under it;
 * any other entry must match the whole path. Built from what
 * app/sites/team/ calls; test/lib/site-rule.test.ts lists why each is here.
 */
export const TEAM_API_PATHS = [
  // Sernia AI: chat, conversations, approvals, general agent settings and push notifications.
  "/api/sernia-ai/",
  // Booking settings, calendar connections (Google sends the browser back to
  // /api/showing-bookings/calendar/callback), showing agents and showings. The
  // public booking API is under it too.
  "/api/showing-bookings/",
  // Rental listing data: the listings, showing rules, Zillow ads and history.
  "/api/rental-listings",
  "/api/rental-listings/",
  // The lead agent's settings.
  "/api/zillow-leads/settings",
  // Message tenants: the sender's contact, the tenants, and sending.
  "/api/contacts/slug/sernia",
  "/api/open_phone/tenants",
  "/api/open_phone/send_message",
  // The scheduler page: list, run and delete jobs.
  "/api/schedulers/",
  // The HITL demo chat and its approval queue.
  "/api/ai-demos/hitl-agent/",
] as const;

/** Where the team's pages are for someone on a host that does not serve them. */
export const TEAM_HOME_URL = "https://serniacapital.com/team";

/**
 * Marks the data on the 404 the root middleware throws for a Sernia host, so
 * the error page and meta can tell it apart from React Router's own 404.
 */
export const SERNIA_NOT_FOUND = "sernia-public-not-found";

export type SerniaNotFound = {
  reason: typeof SERNIA_NOT_FOUND;
  /** Whether `/` on this host is the Sernia home page. */
  homeAtRoot: boolean;
  /** Where the shell's Team link goes from this host. */
  teamHref: string;
};

const PUBLIC_PAGE_PATHS = ["/showings", "/privacy", "/sms-terms"];
const PUBLIC_PAGE_PREFIXES = ["/showings/"];

/**
 * How the root route serves a request.
 *
 * - `portfolio`: the portfolio, without Clerk. `/` on other hosts, every
 *   portfolio page, unknown URLs on other hosts, and in production a team
 *   path on another host (which then answers the portfolio's 404).
 * - `sernia-public`: a Sernia public page, without Clerk.
 * - `sernia-not-found`: any other path on a Sernia host. Answers the Sernia
 *   404, without Clerk.
 * - `team`: a team page, with Clerk.
 */
export type RootRequestMode = "portfolio" | "sernia-public" | "sernia-not-found" | "team";

export type SiteOptions = {
  /** A SERNIA_PUBLIC_HOSTS value; the environment's when omitted. */
  hostsSetting?: string;
  /** Whether the Sernia public pages are served; serniaPublicPagesEnabled() when omitted. */
  pagesEnabled?: boolean;
  /** A RAILWAY_ENVIRONMENT_NAME value; the environment's when omitted. */
  railwayEnvironmentName?: string;
};

type EnvironmentName = "SERNIA_PUBLIC_HOSTS" | "SERNIA_PUBLIC_PAGES_ENABLED" | "RAILWAY_ENVIRONMENT_NAME";

function fromEnvironment(name: EnvironmentName): string | undefined {
  // Read on every call, so a request sees the current value. Also bundled for
  // the browser, where there is no process.
  return typeof process === "undefined" ? undefined : process.env[name];
}

function hostsFromEnvironment(): string | undefined {
  return fromEnvironment("SERNIA_PUBLIC_HOSTS");
}

/** Whether RAILWAY_ENVIRONMENT_NAME names production. */
export function isProduction(
  railwayEnvironmentName: string | undefined = fromEnvironment("RAILWAY_ENVIRONMENT_NAME"),
): boolean {
  return railwayEnvironmentName?.trim().toLowerCase() === "production";
}

/**
 * Whether the Sernia public pages are served. In production
 * (RAILWAY_ENVIRONMENT_NAME is "production") they are off unless
 * SERNIA_PUBLIC_PAGES_ENABLED is "true"; elsewhere they are on unless it is
 * "false". Turning them on in production waits for the page wording to be
 * approved.
 */
export function serniaPublicPagesEnabled(
  setting: string | undefined = fromEnvironment("SERNIA_PUBLIC_PAGES_ENABLED"),
  railwayEnvironmentName: string | undefined = fromEnvironment("RAILWAY_ENVIRONMENT_NAME"),
): boolean {
  const value = setting?.trim().toLowerCase();
  if (isProduction(railwayEnvironmentName)) return value === "true";
  return value !== "false";
}

/** Lowercase host name without port or trailing dot, from a Host header or URL host. */
export function normalizeHost(host: string | null | undefined): string {
  let name = (host ?? "").trim().toLowerCase();
  if (name.startsWith("[")) {
    // IPv6 literal, such as [::1]:5173.
    const end = name.indexOf("]");
    return end === -1 ? name : name.slice(0, end + 1);
  }
  const colon = name.indexOf(":");
  if (colon !== -1) name = name.slice(0, colon);
  return name.endsWith(".") ? name.slice(0, -1) : name;
}

/** The Sernia hosts: SERNIA_PUBLIC_HOSTS (comma separated) when set, else the defaults. */
export function serniaPublicHosts(setting: string | undefined = hostsFromEnvironment()): string[] {
  const configured = (setting ?? "").split(",").map(normalizeHost).filter(Boolean);
  return configured.length > 0 ? configured : [...DEFAULT_SERNIA_PUBLIC_HOSTS];
}

export function isSerniaHost(
  host: string | null | undefined,
  setting: string | undefined = hostsFromEnvironment(),
): boolean {
  const name = normalizeHost(host);
  return name !== "" && serniaPublicHosts(setting).includes(name);
}

function decodePathname(pathname: string): string {
  // Match the way React Router decodes a path before matching routes.
  try {
    return pathname
      .split("/")
      .map((segment) => decodeURIComponent(segment).replace(/\//g, "%2F"))
      .join("/");
  } catch {
    return pathname;
  }
}

/**
 * `/showings`, anything under `/showings/`, `/privacy` and `/sms-terms`.
 * Case and a trailing slash are ignored, as React Router ignores them when
 * matching routes.
 */
export function isSerniaPublicPath(pathname: string): boolean {
  const path = decodePathname(pathname).toLowerCase();
  const trimmed = path.replace(/\/+$/, "") || "/";
  return (
    PUBLIC_PAGE_PATHS.includes(trimmed) ||
    PUBLIC_PAGE_PREFIXES.some((prefix) => path.startsWith(prefix))
  );
}

/** `/team` and anything under `/team/`, ignoring case as React Router does. */
export function isTeamPath(pathname: string): boolean {
  const path = decodePathname(pathname).toLowerCase();
  return path === "/team" || path.startsWith("/team/");
}

/**
 * `/`, the one path whose page depends on the host: the Sernia home page on a
 * Sernia host, the portfolio's home page everywhere else.
 */
export function isHomePath(pathname: string): boolean {
  return pathname.replace(/\/+$/, "") === "";
}

function isRobotsPath(pathname: string): boolean {
  return pathname.toLowerCase() === "/robots.txt";
}

/** Whether a host serves the team's pages: a Sernia host always, any other host outside production. */
export function teamSiteServed(host: string | null | undefined, options: SiteOptions = {}): boolean {
  return isSerniaHost(host, options.hostsSetting) || !isProduction(options.railwayEnvironmentName);
}

/** Where a link to the team's pages goes from this host: `/team` where they are served, else serniacapital.com. */
export function teamHref(url: URL | string, options: SiteOptions = {}): string {
  const parsed = typeof url === "string" ? new URL(url) : url;
  return teamSiteServed(parsed.host, options) ? "/team" : TEAM_HOME_URL;
}

/** See RootRequestMode. */
export function rootRequestMode(url: URL | string, options: SiteOptions = {}): RootRequestMode {
  const parsed = typeof url === "string" ? new URL(url) : url;
  if (isTeamPath(parsed.pathname)) return teamSiteServed(parsed.host, options) ? "team" : "portfolio";
  const serniaHost = isSerniaHost(parsed.host, options.hostsSetting);
  const pagesEnabled = options.pagesEnabled ?? serniaPublicPagesEnabled(undefined, options.railwayEnvironmentName);
  const publicPage = pagesEnabled && isSerniaPublicPath(parsed.pathname);
  if (serniaHost) {
    // robots.txt keeps crawlers out of /team, whether or not the pages are on.
    if (isRobotsPath(parsed.pathname)) return "sernia-public";
    return publicPage || (pagesEnabled && isHomePath(parsed.pathname)) ? "sernia-public" : "sernia-not-found";
  }
  return publicPage ? "sernia-public" : "portfolio";
}

/** True for `/` on a Sernia host while the pages are on: the Sernia home page. */
export function isSerniaHomeRequest(url: URL | string, options: SiteOptions = {}): boolean {
  const parsed = typeof url === "string" ? new URL(url) : url;
  return isHomePath(parsed.pathname) && rootRequestMode(parsed, options) === "sernia-public";
}

/**
 * The address a team request is redirected to, or null. A team path on
 * `www.` of a Sernia host goes to the same URL on the host without `www.`,
 * so sign-in, notifications and the calendar connection have one origin.
 * The Sernia public pages answer on `www.` as they are.
 *
 * The address has no scheme (`//serniacapital.com/team`), so the browser keeps
 * the one it used: behind Railway's proxy the server sees plain http.
 */
export function teamRedirect(url: URL | string, options: SiteOptions = {}): string | null {
  const parsed = typeof url === "string" ? new URL(url) : url;
  if (!isTeamPath(parsed.pathname)) return null;
  const host = normalizeHost(parsed.host);
  if (!host.startsWith("www.")) return null;
  const bare = host.slice("www.".length);
  const hosts = serniaPublicHosts(options.hostsSetting);
  if (!hosts.includes(host) || !hosts.includes(bare)) return null;
  const target = new URL(parsed.href);
  target.hostname = bare;
  return `//${target.host}${target.pathname}${target.search}${target.hash}`;
}

function isSerniaApiPath(pathname: string): boolean {
  return (
    pathname.startsWith(SERNIA_PUBLIC_API_PREFIX) ||
    TEAM_API_PATHS.some((entry) => (entry.endsWith("/") ? pathname.startsWith(entry) : pathname === entry))
  );
}

/**
 * The /api proxy rule. Other hosts proxy every /api path, as before. A Sernia
 * host proxies the public booking API and the paths in TEAM_API_PATHS, checked
 * both as received and after resolving `..` the way fetch() resolves it
 * before forwarding. Everything else answers 404.
 *
 * @param requestUrl The path and query as received, such as Express's `req.originalUrl`.
 */
export function isApiProxyAllowed(
  host: string | null | undefined,
  requestUrl: string,
  setting: string | undefined = hostsFromEnvironment(),
): boolean {
  if (!isSerniaHost(host, setting)) return true;
  let resolved: string;
  try {
    resolved = new URL(`http://proxy.invalid${requestUrl}`).pathname;
  } catch {
    return false;
  }
  const received = requestUrl.split(/[?#]/, 1)[0];
  return isSerniaApiPath(received) && isSerniaApiPath(resolved);
}
