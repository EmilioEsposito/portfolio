import { Link } from "react-router";

import { CallLine } from "~/components/sernia-public/call-line";
import { SerniaPublicShell } from "~/components/sernia-public/shell";
import { SERNIA_NAME, SERNIA_SITE_URL } from "~/lib/sernia-public-seo";

/** The residents' Buildium portal, where serniacapital.com redirected before this page. */
export const RESIDENT_PORTAL_URL = "https://serniacapital.managebuilding.com/";
/** Stays a Cloudflare redirect to the application form; the web app has no page there. */
export const APPLY_PATH = "/apply";

export type SerniaHomeData = {
  /** Whether public booking is on. While it is off the call line is left out: its number route answers 404. */
  bookingOpen: boolean;
  turnstileSiteKey: string | null;
};

export const SERNIA_HOME_DESCRIPTION =
  "Sernia Capital rents apartments. Residents sign in here; anyone looking for a home can book a showing or apply.";

/** Meta for the Sernia home page, which only a Sernia host serves at /. */
export function serniaHomeMeta() {
  return [
    { title: SERNIA_NAME },
    { name: "description", content: SERNIA_HOME_DESCRIPTION },
    { property: "og:title", content: SERNIA_NAME },
    { property: "og:description", content: SERNIA_HOME_DESCRIPTION },
    { property: "og:url", content: `${SERNIA_SITE_URL}/` },
    { property: "og:type", content: "website" },
    { property: "og:site_name", content: SERNIA_NAME },
    { tagName: "link", rel: "canonical", href: `${SERNIA_SITE_URL}/` },
  ];
}

const choice =
  "flex min-h-14 w-full items-center justify-between gap-4 rounded-lg border px-5 py-4 text-base font-medium hover:bg-muted/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";

/**
 * serniacapital.com's home page: Resident sign-in first, since residents type
 * the domain to reach the portal, then Book a showing and Apply. The shell's
 * footer carries the Equal Housing Opportunity line and the policy links.
 */
export function SerniaHome({ data }: { data: SerniaHomeData }) {
  return (
    <SerniaPublicShell>
      <div className="space-y-8">
        <h1 className="sr-only">{SERNIA_NAME}</h1>
        <nav aria-label="Sernia Capital">
          <ul className="space-y-3">
            <li>
              <a href={RESIDENT_PORTAL_URL} className={choice}>
                Resident sign-in
                <Arrow />
              </a>
            </li>
            <li>
              <Link to="/showings" className={choice}>
                Book a showing
                <Arrow />
              </Link>
            </li>
            <li>
              <a href={APPLY_PATH} className={choice}>
                Apply
                <Arrow />
              </a>
            </li>
          </ul>
        </nav>
        {data.bookingOpen && <CallLine siteKey={data.turnstileSiteKey} lead="Questions?" tail="." />}
      </div>
    </SerniaPublicShell>
  );
}

function Arrow() {
  return (
    <span aria-hidden="true" className="text-muted-foreground">
      →
    </span>
  );
}
