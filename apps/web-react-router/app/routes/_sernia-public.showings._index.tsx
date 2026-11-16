import { data, Link } from "react-router";

import type { Route } from "./+types/_sernia-public.showings._index";
import { CallLine } from "~/components/sernia-public/call-line";
import { SerniaPublicError } from "~/components/sernia-public/shell";
import { SERNIA_NAME, serniaPageMeta } from "~/lib/sernia-public-seo";
import { unitHeading, unitPath, unitTerms } from "~/lib/showing-units";
import { readUnitsOnOffer } from "~/lib/showing-units.server";

// The units on offer, each a link to its booking page. Read on the server, so
// the list is in the page for people and search engines alike. While public
// booking is off the API answers 404 and the page says no unit is open for
// online booking, with no call line (its number route is off too).

export function meta({ error }: Route.MetaArgs) {
  if (error) return [{ title: `Booking unavailable | ${SERNIA_NAME}` }, { name: "robots", content: "noindex" }];
  return serniaPageMeta({
    title: "Book a showing",
    description: "Sernia Capital's units for rent, with the address, asking rent and lease start of each, and a link to book an in-person showing.",
    path: "/showings",
  });
}

export function headers() {
  return { "Cache-Control": "no-store" };
}

export async function loader() {
  const state = await readUnitsOnOffer();
  if (state.status === "unavailable") throw data(null, { status: 503, statusText: "Service Unavailable" });
  return state;
}

export default function ShowingsPage({ loaderData }: Route.ComponentProps) {
  return (
    <div className="space-y-8">
      <header className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Book a showing</h1>
        {loaderData.status === "open" && loaderData.units.length > 0 && (
          <p className="text-muted-foreground">Choose a unit to see its open times.</p>
        )}
      </header>
      {loaderData.status === "closed" ? (
        <p className="leading-7">No units are open for online booking right now.</p>
      ) : loaderData.units.length === 0 ? (
        <div className="space-y-4">
          <p className="leading-7">No units are open for showings right now.</p>
          <CallLine siteKey={loaderData.turnstileSiteKey} lead="Questions?" tail="." />
        </div>
      ) : (
        <ul className="divide-y rounded-lg border" aria-label="Units for rent">
          {loaderData.units.map((item) => {
            const heading = unitHeading(item);
            const terms = unitTerms(item);
            return (
              <li key={item.unit}>
                <Link
                  to={unitPath(item.unit)}
                  className="group flex items-center justify-between gap-4 px-4 py-4 hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring sm:px-5"
                >
                  <span className="min-w-0 space-y-1">
                    <span className="block font-medium group-hover:underline group-hover:underline-offset-4">
                      {heading.title}
                      {heading.detail && <span className="font-normal text-muted-foreground">, {heading.detail}</span>}
                    </span>
                    {terms && <span className="block text-sm text-muted-foreground">{terms}</span>}
                    {!item.has_open_times && (
                      <span className="block text-sm text-muted-foreground">No open times online right now</span>
                    )}
                  </span>
                  <span aria-hidden="true" className="shrink-0 text-muted-foreground">
                    →
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  return <SerniaPublicError error={error} />;
}
