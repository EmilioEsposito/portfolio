import { useState } from "react";
import { data, isRouteErrorResponse, useSearchParams } from "react-router";

import type { Route } from "./+types/_sernia-public.showings.$unit";
import { ShowingBooked } from "~/components/sernia-public/showing-booked";
import { ShowingBookingForm } from "~/components/sernia-public/showing-booking-form";
import { backendUrl } from "~/lib/backend-url.server";
import { type BookingResult, PUBLIC_API, type UnitPage, unitSummary } from "~/lib/showing-booking-page";
import { SERNIA_CONTACT_EMAIL, SERNIA_NAME } from "~/lib/sernia-public-seo";

// Book an in-person showing of one unit. The unit is read here on the server,
// so a unit that is not on offer (or booking switched off, which the API
// answers the same way) is a 404 page. The booking, a corrected mobile and
// the team's number go from the browser straight to the public API, so the
// person's own IP address, user agent and Signature-Agent header reach it.

const NOINDEX = { name: "robots", content: "noindex" };

export function meta({ params, error }: Route.MetaArgs) {
  if (error) {
    const gone = isRouteErrorResponse(error) && error.status === 404;
    return [{ title: `${gone ? "Unit not open for showings" : "Booking unavailable"} | ${SERNIA_NAME}` }, NOINDEX];
  }
  return [{ title: `Book a showing of unit ${params.unit} | ${SERNIA_NAME}` }, NOINDEX];
}

export function headers() {
  return { "X-Robots-Tag": "noindex", "Cache-Control": "no-store" };
}

export async function loader({ params }: Route.LoaderArgs) {
  let response: Response;
  try {
    response = await fetch(`${backendUrl()}${PUBLIC_API}/units/${encodeURIComponent(params.unit)}`, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    throw data(null, { status: 503, statusText: "Service Unavailable" });
  }
  if (response.status === 404) throw data(null, { status: 404, statusText: "Not Found" });
  if (!response.ok) throw data(null, { status: 503, statusText: "Service Unavailable" });
  return (await response.json()) as UnitPage;
}

export default function ShowingBookingPage({ loaderData }: Route.ComponentProps) {
  const [searchParams] = useSearchParams();
  const [booked, setBooked] = useState<BookingResult | null>(null);
  const leadToken = searchParams.get("t");

  if (booked) {
    return <ShowingBooked result={booked} siteKey={loaderData.turnstile_site_key} />;
  }

  return (
    <div className="space-y-8">
      <header className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">Book an in-person showing</h1>
        <p className="text-muted-foreground">{unitSummary(loaderData)}</p>
      </header>
      <ShowingBookingForm initialUnit={loaderData} leadToken={leadToken} onBooked={setBooked} />
    </div>
  );
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  const gone = isRouteErrorResponse(error) && error.status === 404;
  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-semibold tracking-tight">
        {gone ? "This unit is not open for showings" : "Booking is not available right now"}
      </h1>
      <p className="leading-7 text-muted-foreground">
        {gone
          ? "It may have been rented, or the link may be incomplete. "
          : "Please try again in a few minutes. "}
        Questions? Email{" "}
        <a href={`mailto:${SERNIA_CONTACT_EMAIL}`} className="font-medium text-foreground underline underline-offset-4">
          {SERNIA_CONTACT_EMAIL}
        </a>
        .
      </p>
    </div>
  );
}
