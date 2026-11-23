import { useState } from "react";
import { data, isRouteErrorResponse, useSearchParams } from "react-router";

import type { Route } from "./+types/unit";
import { ShowingAlreadyBooked, ShowingBooked } from "~/sites/sernia-public/components/showing-booked";
import { ShowingBookingForm } from "~/sites/sernia-public/components/showing-booking-form";
import { backendUrl } from "~/sites/sernia-public/lib/backend-url.server";
import {
  type BookingPageData,
  type BookingResult,
  type LeadBooking,
  PUBLIC_API,
  type UnitPage,
  leadTokenOf,
  unitSummary,
} from "~/sites/sernia-public/lib/showing-booking-page";
import { SERNIA_CONTACT_EMAIL, SERNIA_NAME } from "~/sites/sernia-public/lib/seo";
import { unitForPage } from "~/sites/sernia-public/lib/showing-units.server";

// Book an in-person showing of one unit. The unit is read here on the server,
// so a unit that is not on offer (or booking switched off, which the API
// answers the same way) is a 404 page. The booking, a corrected mobile and
// the team's number go from the browser straight to the public API, so the
// person's own IP address, user agent and Signature-Agent header reach it.
// A lead whose showing the lead agent booked in their email thread opens the
// page with their own link (`?t=`): the loader then finds that place, and the
// page shows the time already booked and asks only for their details. Once
// their details are in, it says they are booked, with when and where. Their link
// works for a unit that is no longer open to others too: the unit then answers
// 404, and what the page needs of it comes with their place instead.

const NOINDEX = { name: "robots", content: "noindex" };

export function meta({ params, error, loaderData }: Route.MetaArgs) {
  if (error) {
    const gone = isRouteErrorResponse(error) && error.status === 404;
    return [{ title: `${gone ? "Unit not open for showings" : "Booking unavailable"} | ${SERNIA_NAME}` }, NOINDEX];
  }
  if (loaderData?.lead_booking?.details_added) {
    return [{ title: `You're booked to see unit ${params.unit} | ${SERNIA_NAME}` }, NOINDEX];
  }
  return [{ title: `Book a showing of unit ${params.unit} | ${SERNIA_NAME}` }, NOINDEX];
}

export function headers() {
  return { "X-Robots-Tag": "noindex", "Cache-Control": "no-store" };
}

export async function loader({ params, request }: Route.LoaderArgs): Promise<BookingPageData> {
  const unitPath = `${backendUrl()}${PUBLIC_API}/units/${encodeURIComponent(params.unit)}`;
  const found = await unitForPage(params.unit, request);
  if (found.status === "unavailable") throw data(null, { status: 503, statusText: "Service Unavailable" });
  const token = leadTokenOf(new URL(request.url).searchParams.get("t"));
  // The place booked for this lead's email thread, when the link's token has one
  // at this unit: the page then asks only for their details, or says they are
  // booked when the details are in. Without one, it is the normal booking form.
  const place = token ? await withToken<LeadBooking>(`${unitPath}/lead-booking`, token) : null;
  const unit =
    found.status === "open"
      ? found.page
      : token && place && (await withToken<UnitPage>(`${unitPath}/lead-booking/unit`, token));
  if (!unit) throw data(null, { status: 404, statusText: "Not Found" });
  return { ...unit, lead_booking: place };
}

/** Asks the API with the lead's token, in the request body, never in its path or query. Null for anything but a 200. */
async function withToken<T>(path: string, token: string): Promise<T | null> {
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { accept: "application/json", "content-type": "application/json" },
      body: JSON.stringify({ lead_token: token }),
      signal: AbortSignal.timeout(15_000),
    });
    return response.ok ? ((await response.json()) as T) : null;
  } catch {
    return null;
  }
}

export default function ShowingBookingPage({ loaderData }: Route.ComponentProps) {
  const [searchParams] = useSearchParams();
  const [booked, setBooked] = useState<BookingResult | null>(null);
  const leadToken = searchParams.get("t");
  const { lead_booking: leadPlace, ...unit } = loaderData;

  if (booked) {
    return <ShowingBooked result={booked} siteKey={unit.turnstile_site_key} />;
  }
  if (leadPlace?.details_added) {
    return <ShowingAlreadyBooked place={leadPlace} siteKey={unit.turnstile_site_key} />;
  }

  return (
    <div className="space-y-8">
      <header className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
          {leadPlace ? "Add your details" : "Book an in-person showing"}
        </h1>
        <p className="text-muted-foreground">{unitSummary(unit)}</p>
      </header>
      <ShowingBookingForm initialUnit={unit} leadToken={leadToken} leadPlace={leadPlace} onBooked={setBooked} />
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
