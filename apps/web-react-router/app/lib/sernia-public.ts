/**
 * Which requests belong to Sernia Capital's public pages (serniacapital.com).
 *
 * Those pages need no sign-in and load no Clerk: Clerk's production keys
 * belong to eesposito.com. The root route, server.js and the Vite dev proxy
 * all decide from this module. server.js imports it directly, relying on
 * Node's built-in TypeScript type stripping, so it must keep no imports and
 * use only syntax that can be erased (no enums or namespaces).
 */

/** Hosts that serve only the Sernia public pages. SERNIA_PUBLIC_HOSTS replaces this list. */
export const DEFAULT_SERNIA_PUBLIC_HOSTS = [
  "serniacapital.com",
  "www.serniacapital.com",
  "serniacapital.localhost",
] as const;

/** The only API paths the /api proxy forwards for a Sernia host. */
export const SERNIA_PUBLIC_API_PREFIX = "/api/showing-bookings/public/";

/**
 * Data on the 404 the root middleware throws for a Sernia host, so the error
 * page and meta can tell it apart from React Router's own 404.
 */
export const SERNIA_NOT_FOUND = "sernia-public-not-found";

const PUBLIC_PAGE_PATHS = ["/showings", "/privacy", "/sms-terms"];
const PUBLIC_PAGE_PREFIXES = ["/showings/"];

/** How the root route serves a page request. */
export type RootRequestMode = "app" | "sernia-public" | "sernia-not-found";

function hostsFromEnvironment(): string | undefined {
  // Also bundled for the browser, where there is no process.
  return typeof process === "undefined" ? undefined : process.env.SERNIA_PUBLIC_HOSTS;
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

/**
 * - `sernia-not-found`: a Sernia host asked for anything else; it answers 404.
 * - `sernia-public`: a Sernia public path on any host; served without Clerk.
 * - `app`: everything else, served as before.
 */
export function rootRequestMode(
  url: URL | string,
  setting: string | undefined = hostsFromEnvironment(),
): RootRequestMode {
  const parsed = typeof url === "string" ? new URL(url) : url;
  const serniaHost = isSerniaHost(parsed.host, setting);
  const publicPath = isSerniaPublicPath(parsed.pathname);
  if (serniaHost && !publicPath) return "sernia-not-found";
  if (serniaHost || publicPath) return "sernia-public";
  return "app";
}

/** True when the host is a Sernia host, or the path is a Sernia public path on any host. */
export function isSerniaPublicRequest(
  url: URL | string,
  setting: string | undefined = hostsFromEnvironment(),
): boolean {
  return rootRequestMode(url, setting) !== "app";
}

function isUnderPublicApi(pathname: string): boolean {
  return pathname.startsWith(SERNIA_PUBLIC_API_PREFIX);
}

/**
 * The /api proxy rule. Other hosts proxy every /api path, as before. A Sernia
 * host proxies only paths under SERNIA_PUBLIC_API_PREFIX, checked both as
 * received and after resolving `..` the way fetch() resolves it before
 * forwarding.
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
  return isUnderPublicApi(requestUrl) && isUnderPublicApi(resolved);
}
