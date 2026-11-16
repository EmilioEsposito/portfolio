import { data, isRouteErrorResponse } from "react-router";

import type { Route } from "./+types/_sernia-public.showings.cancel.$token";
import { ShowingCancel } from "~/components/sernia-public/showing-cancel";
import { backendUrl } from "~/lib/backend-url.server";
import { type BookingView, PUBLIC_API } from "~/lib/showing-booking-page";
import { cancelCopy, cancelState } from "~/lib/showing-cancel-page";
import { SERNIA_CONTACT_EMAIL, SERNIA_NAME } from "~/lib/sernia-public-seo";

// The page a cancel link opens: the booking and a Cancel button. Opening it
// never cancels anything, because phones and mail scanners open links to
// preview them; only the button does. The booking is read here on the server,
// so an unknown link (or booking switched off, which the API answers the same
// way) is a 404 page. The URL holds the cancel token, so the page is kept out
// of search results and sends no referrer; the token reaches the API only in
// a request body.

const NOINDEX = { name: "robots", content: "noindex" };
const NO_REFERRER = { name: "referrer", content: "no-referrer" };

export function meta({ loaderData, error }: Route.MetaArgs) {
  if (error || !loaderData) {
    const gone = isRouteErrorResponse(error) && error.status === 404;
    return [{ title: `${gone ? "Booking not found" : "Cancelling unavailable"} | ${SERNIA_NAME}` }, NOINDEX, NO_REFERRER];
  }
  const { title } = cancelCopy(cancelState(loaderData), loaderData.unit);
  return [{ title: `${title} | ${SERNIA_NAME}` }, NOINDEX, NO_REFERRER];
}

export function headers() {
  return { "X-Robots-Tag": "noindex", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };
}

export async function loader({ params }: Route.LoaderArgs) {
  let response: Response;
  try {
    response = await fetch(`${backendUrl()}${PUBLIC_API}/bookings/lookup`, {
      method: "POST",
      headers: { accept: "application/json", "content-type": "application/json" },
      body: JSON.stringify({ cancel_token: params.token }),
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    throw data(null, { status: 503, statusText: "Service Unavailable", headers: { "Referrer-Policy": "no-referrer" } });
  }
  if (response.status === 404) {
    throw data(null, { status: 404, statusText: "Not Found", headers: { "Referrer-Policy": "no-referrer" } });
  }
  if (!response.ok) {
    throw data(null, { status: 503, statusText: "Service Unavailable", headers: { "Referrer-Policy": "no-referrer" } });
  }
  return (await response.json()) as BookingView;
}

export default function ShowingCancelPage({ loaderData, params }: Route.ComponentProps) {
  // Keyed by token, so moving between two links starts afresh.
  return <ShowingCancel key={params.token} token={params.token} initial={loaderData} />;
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  const gone = isRouteErrorResponse(error) && error.status === 404;
  return (
    <div className="space-y-3">
      <h1 className="text-2xl font-semibold tracking-tight">
        {gone ? "We couldn't find this booking" : "Cancelling is not available right now"}
      </h1>
      <p className="leading-7 text-muted-foreground">
        {gone ? "The link may be incomplete. " : "Please try again in a few minutes. "}
        Questions? Email{" "}
        <a href={`mailto:${SERNIA_CONTACT_EMAIL}`} className="font-medium text-foreground underline underline-offset-4">
          {SERNIA_CONTACT_EMAIL}
        </a>
        .
      </p>
    </div>
  );
}
