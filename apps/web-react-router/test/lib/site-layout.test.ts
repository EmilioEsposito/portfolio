import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, posix } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { routeTable } from "./route-table";

// The web app is laid out by site (README, "Layout"). Code under one site's
// folder never imports another site's folder; code more than one site uses
// lives in app/components, app/lib or app/hooks. The glue directly under
// app/sites/ may import any site. Only the team's pages use Clerk: nothing the
// portfolio or the Sernia public pages import loads it.

const APP = fileURLToPath(new URL("../../app/", import.meta.url));

/** Source files under a folder of app/, as paths relative to app/. */
function sourceFiles(folder: string): string[] {
  return readdirSync(`${APP}${folder}`, { recursive: true, encoding: "utf8" })
    .filter((name) => /\.tsx?$/.test(name))
    .map((name) => posix.join(folder, name.split("\\").join("/")))
    .sort();
}

const IMPORT = /(?:\bfrom|\bimport)\s*\(?\s*["']([^"']+)["']/g;

/** Every module a source file imports, static or dynamic. */
function importsOf(source: string): string[] {
  return [...source.matchAll(IMPORT)].map((match) => match[1]);
}

/** What an import points at, relative to app/; null for a package. */
function target(file: string, specifier: string): string | null {
  if (specifier.startsWith("~/")) return specifier.slice(2);
  if (specifier.startsWith(".")) return posix.normalize(posix.join(dirname(file), specifier));
  return null;
}

/** The site a path under app/ belongs to, or null for shared code and the glue. */
function siteOf(path: string): string | null {
  const [top, site, ...rest] = path.split("/");
  return top === "sites" && rest.length > 0 ? site : null;
}

/** The module a relative or `~/` import resolves to, if it is a source file. */
function resolve(path: string, files: Set<string>): string | undefined {
  return [path, `${path}.ts`, `${path}.tsx`, `${path}/index.ts`, `${path}/index.tsx`].find((name) => files.has(name));
}

const SITES = readdirSync(`${APP}sites`, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort();

const ALL_FILES = new Set(sourceFiles(""));
const source = (file: string) => readFileSync(`${APP}${file}`, "utf8");

test("the sites are portfolio, sernia-public and team", () => {
  assert.deepEqual(SITES, ["portfolio", "sernia-public", "team"]);
});

test("no file under one site imports another site's folder", () => {
  const crossings: string[] = [];
  let checked = 0;
  for (const site of SITES) {
    for (const file of sourceFiles(`sites/${site}`)) {
      for (const specifier of importsOf(source(file))) {
        const to = target(file, specifier);
        if (to === null) continue;
        checked += 1;
        const other = siteOf(to);
        if (other !== null && other !== site) crossings.push(`${file} imports ${specifier}`);
      }
    }
  }
  assert.ok(checked > 50, `expected the sites' own imports, found ${checked}`);
  assert.deepEqual(crossings, [], "move what both sites use to app/components, app/lib or app/hooks");
});

test("the import check sees a crossing", () => {
  const file = "sites/sernia-public/components/example.tsx";
  const found = importsOf(`import { A } from "~/sites/team/lib/a";\nconst b = await import("../../portfolio/lib/b");`)
    .map((specifier) => siteOf(target(file, specifier)!));
  assert.deepEqual(found, ["team", "portfolio"]);
});

/** Every `@clerk/*` import reachable from a site's files, through shared code and the glue. */
function clerkImportsReachedFrom(files: string[]): { reached: number; clerk: string[] } {
  const via = new Map<string, string>();
  const queue = [...files];
  for (const file of queue) via.set(file, file);
  const clerk: string[] = [];
  while (queue.length > 0) {
    const file = queue.shift()!;
    for (const specifier of importsOf(source(file))) {
      if (specifier.startsWith("@clerk/")) {
        clerk.push(`${file} imports ${specifier} (reached from ${via.get(file)})`);
        continue;
      }
      const to = target(file, specifier);
      const next = to === null ? undefined : resolve(to, ALL_FILES);
      if (next && !via.has(next)) {
        via.set(next, via.get(file)!);
        queue.push(next);
      }
    }
  }
  return { reached: via.size, clerk };
}

for (const site of ["portfolio", "sernia-public"]) {
  test(`nothing the ${site} site imports loads Clerk`, () => {
    // Followed through shared code and the glue, not only the site's own files.
    const files = sourceFiles(`sites/${site}`);
    const { reached, clerk } = clerkImportsReachedFrom(files);
    assert.ok(reached > files.length, "expected shared code to be followed");
    assert.deepEqual(clerk, []);
  });
}

test("the Clerk check sees an import of @clerk/*, also through shared code", () => {
  assert.deepEqual(importsOf(`import { useAuth } from "@clerk/react-router";`), ["@clerk/react-router"]);
  // The team's pages do use Clerk, so the check is not blind to it.
  const { clerk } = clerkImportsReachedFrom(sourceFiles("sites/team/routes"));
  assert.ok(clerk.length > 0);
});

test("every route module in a site's routes folder is in app/routes.ts", () => {
  // Routes are listed explicitly, so a file added here serves nothing until it is.
  const listed = new Set(routeTable().map((route) => route.file));
  const missing = SITES.flatMap((site) => sourceFiles(`sites/${site}/routes`)).filter((file) => !listed.has(file));
  assert.deepEqual(missing, []);
});

test("the Sernia public routes are exactly the routes under the Sernia public layout", () => {
  const layout = "sites/sernia-public/routes/layout.tsx";
  for (const route of routeTable()) {
    const inFolder = route.file.startsWith("sites/sernia-public/");
    const underLayout = route.file === layout || route.parents.includes(layout);
    assert.equal(inFolder, underLayout, `${route.file} is ${underLayout ? "" : "not "}under the Sernia public layout`);
  }
});
