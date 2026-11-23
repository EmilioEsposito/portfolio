import type { RouteConfigEntry } from "@react-router/dev/routes";

import routes from "~/routes";

/** One route from app/routes.ts. */
export type RouteRow = {
  /** The route module, relative to app/. */
  file: string;
  /** The URL path it serves, such as `/showings/:unit`. A pathless layout serves its parent's. */
  path: string;
  /** The route modules above it, outermost first. */
  parents: string[];
};

function join(parent: string, path: string | undefined): string {
  if (!path) return parent;
  return parent === "/" ? `/${path}` : `${parent}/${path}`;
}

function flatten(entries: RouteConfigEntry[], parentPath: string, parents: string[]): RouteRow[] {
  return entries.flatMap((entry) => {
    const path = join(parentPath, entry.path);
    return [
      { file: entry.file, path, parents },
      ...flatten(entry.children ?? [], path, [...parents, entry.file]),
    ];
  });
}

/** Every route in app/routes.ts, parents before children. */
export function routeTable(): RouteRow[] {
  return flatten(routes, "/", []);
}

/** A URL the route serves: each param and splat replaced by a sample value. */
export function sampleUrl(path: string): string {
  return path
    .split("/")
    .map((segment) => (segment.startsWith(":") || segment === "*" ? "sample" : segment))
    .join("/");
}
