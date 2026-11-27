import { clearSiteCacheResponse } from "~/sites/team/lib/forget-old-home-redirect";

// The team layout's one-time background request; see
// ~/sites/team/lib/forget-old-home-redirect. It only clears the caller's own
// browser cache, so it needs no sign-in.
export function loader() {
  return clearSiteCacheResponse();
}
