import { redirect } from "react-router";

import type { Route } from "./+types/calendar";
import { backendUrl } from "~/sites/sernia-public/lib/backend-url.server";
import { PUBLIC_API } from "~/sites/sernia-public/lib/showing-booking-page";
import { calendarFileToken, cancelPath } from "~/sites/sernia-public/lib/showing-cancel-page";

// The confirmation email's "Add to Apple or Outlook calendar" link:
// /showings/calendar/<cancel token>.ics. It answers the booking as an iCalendar
// file while it is booked and ahead. For a booking that is cancelled, started or
// unknown it sends the person to the cancel page, which says which. As on that
// page, the token reaches the API only in a request body, and the answer is
// kept out of caches, search results and referrers.

const PRIVATE = { "Cache-Control": "no-store", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer" };

export async function loader({ params }: Route.LoaderArgs) {
  const token = calendarFileToken(params.file);
  if (token === null) throw new Response("Not Found", { status: 404, headers: PRIVATE });
  let response: Response;
  try {
    response = await fetch(`${backendUrl()}${PUBLIC_API}/bookings/calendar`, {
      method: "POST",
      headers: { accept: "text/calendar, application/json", "content-type": "application/json" },
      body: JSON.stringify({ cancel_token: token }),
      signal: AbortSignal.timeout(15_000),
    });
  } catch {
    throw new Response("The calendar file is not available right now. Please try again in a few minutes.", {
      status: 503,
      headers: { ...PRIVATE, "Content-Type": "text/plain; charset=utf-8" },
    });
  }
  if (response.status === 404 || response.status === 409) {
    throw redirect(cancelPath(token), { status: 303, headers: PRIVATE });
  }
  if (!response.ok) {
    throw new Response("The calendar file is not available right now. Please try again in a few minutes.", {
      status: 503,
      headers: { ...PRIVATE, "Content-Type": "text/plain; charset=utf-8" },
    });
  }
  return new Response(await response.text(), {
    headers: {
      ...PRIVATE,
      "Content-Type": "text/calendar; charset=utf-8",
      "Content-Disposition": 'attachment; filename="sernia-showing.ics"',
    },
  });
}
