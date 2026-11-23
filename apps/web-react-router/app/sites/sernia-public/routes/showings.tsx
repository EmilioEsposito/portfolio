import { Suspense } from "react";
import { Await, Link } from "react-router";

import type { Route } from "./+types/showings";
import { CallLine } from "~/sites/sernia-public/components/call-line";
import { ContactEmail } from "~/sites/sernia-public/components/legal-page";
import { SerniaPublicError } from "~/sites/sernia-public/components/shell";
import { SERNIA_NAME, serniaPageMeta } from "~/sites/sernia-public/lib/seo";
import { unitHeading, unitPath, unitTerms, type UnitsState } from "~/sites/sernia-public/lib/showing-units";
import { listForPage } from "~/sites/sernia-public/lib/showing-units.server";

// The units on offer, each a link to its booking page. Read on the server, so
// the list is in the page for people and search engines alike. The API reads
// the team's calendars for it, so the page opens without waiting: a list not
// read yet (read-ahead.server.ts) follows in the same response. While public
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

export async function loader({ request }: Route.LoaderArgs): Promise<{ list: UnitsState | Promise<UnitsState> }> {
  const list = listForPage(request);
  // One already read is in the page from the start, with no placeholder before it.
  const known: UnitsState[] = [];
  void list.then((state) => known.push(state));
  await new Promise((resolve) => setImmediate(resolve));
  return { list: known[0] ?? list };
}

export default function ShowingsPage({ loaderData }: Route.ComponentProps) {
  return (
    <div className="space-y-8">
      <Suspense fallback={<Reading />}>
        {/* React Router gives up on a list that takes longer than 5 seconds. */}
        <Await resolve={loaderData.list} errorElement={<Unavailable />}>
          {(list) => <Units list={list} />}
        </Await>
      </Suspense>
    </div>
  );
}

function Heading({ children }: { children?: React.ReactNode }) {
  return (
    <header className="space-y-1.5">
      <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Book a showing</h1>
      {children}
    </header>
  );
}

/** While the list is read: the heading, and the shape of a row. */
function Reading() {
  return (
    <>
      <Heading />
      <div className="rounded-lg border px-4 py-4 sm:px-5">
        <p role="status" className="sr-only">
          Getting the units…
        </p>
        <div aria-hidden="true" className="space-y-2.5 motion-safe:animate-pulse">
          <div className="h-4 w-2/5 rounded bg-foreground/10" />
          <div className="h-3.5 w-3/5 rounded bg-foreground/10" />
        </div>
      </div>
    </>
  );
}

function Unavailable() {
  return (
    <>
      <Heading />
      <p className="leading-7">
        Booking is not available right now. Please try again in a few minutes. Questions? Email <ContactEmail />.
      </p>
    </>
  );
}

function Units({ list }: { list: UnitsState }) {
  if (list.status === "unavailable") return <Unavailable />;
  if (list.status === "closed") {
    return (
      <>
        <Heading />
        <p className="leading-7">No units are open for online booking right now.</p>
      </>
    );
  }
  if (list.units.length === 0) {
    return (
      <>
        <Heading />
        <div className="space-y-4">
          <p className="leading-7">No units are open for showings right now.</p>
          <CallLine siteKey={list.turnstileSiteKey} lead="Questions?" tail="." />
        </div>
      </>
    );
  }
  return (
    <>
      <Heading>
        <p className="text-muted-foreground">Choose a unit to see its open times.</p>
      </Heading>
      <ul className="divide-y rounded-lg border" aria-label="Units for rent">
        {list.units.map((item) => {
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
    </>
  );
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  return <SerniaPublicError error={error} />;
}
