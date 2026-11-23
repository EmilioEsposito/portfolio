import { data, Outlet } from "react-router";

import type { Route } from "./+types/layout";
import { SerniaPublicShell } from "~/sites/sernia-public/components/shell";
import { rootRequestMode } from "~/sites/site";

// Pathless layout for Sernia Capital's public pages. Each page under it must
// sit at a Sernia public path (see ~/sites/site), so the root serves it
// without Clerk; test/lib/sernia-public.test.ts checks app/routes.ts.

// While the pages are switched off (SERNIA_PUBLIC_PAGES_ENABLED), their paths
// on other hosts take the app's usual path through Clerk, and this answers
// the same 404 an unknown URL gets. A Sernia host has already answered its
// own 404 in the root middleware.
export const middleware: Route.MiddlewareFunction[] = [
  (args, next) => {
    if (rootRequestMode(args.url) !== "sernia-public") {
      throw data(null, { status: 404, statusText: "Not Found" });
    }
    return next();
  },
];

// React Router's client skips the server when no loader needs to run. This
// loader makes entering these pages from elsewhere in the app always ask the
// server, so the middleware above decides there too.
export function loader() {
  return null;
}

export default function SerniaPublicLayout() {
  return (
    <SerniaPublicShell>
      <Outlet />
    </SerniaPublicShell>
  );
}
