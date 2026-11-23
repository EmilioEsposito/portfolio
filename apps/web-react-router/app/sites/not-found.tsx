import { data } from "react-router";

// Unknown URLs land here instead of on React Router's built-in 404, which runs
// no app code, so the root middleware runs for them too. On a Sernia host that
// gives them the Sernia 404 rather than a page carrying the personal site's
// title and preview tags. On other hosts they get the same 404 page as before.
function notFound(): never {
  throw data(null, { status: 404, statusText: "Not Found" });
}

export const loader = notFound;
export const action = notFound;

// Never rendered, since the loader always throws to the root error boundary.
// The default export keeps this a page route rather than a resource route.
export default function NotFound() {
  return null;
}
