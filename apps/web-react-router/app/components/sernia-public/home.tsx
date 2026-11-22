import { Link } from "react-router";

import { CallLine } from "~/components/sernia-public/call-line";
import { Street } from "~/components/sernia-public/drawings";
import { SERNIA_WIDE_COLUMN, SerniaPublicShell } from "~/components/sernia-public/shell";
import { SERNIA_NAME, serniaUrl } from "~/lib/sernia-public-seo";
import { cn } from "~/lib/utils";

/** The residents' Buildium portal, where serniacapital.com redirected before this page. */
export const RESIDENT_PORTAL_URL = "https://serniacapital.managebuilding.com/";
/** Stays a Cloudflare redirect to the application form; the web app has no page there. */
export const APPLY_PATH = "/apply";
export const ZILLOW_REVIEWS_URL = "https://www.zillow.com/profile/Sernia%20Capital";

export type SerniaHomeData = {
  /** Whether public booking is on. While it is off the call line is left out: its number route answers 404. */
  bookingOpen: boolean;
  turnstileSiteKey: string | null;
};

const HEADING = "Apartments in Bloomfield and Shadyside";
export const SERNIA_HOME_TITLE = `${SERNIA_NAME} | ${HEADING}`;
export const SERNIA_HOME_DESCRIPTION =
  "Sernia Capital rents apartments in Bloomfield, Shadyside and around Pittsburgh. Book a showing, apply, or sign in to the resident portal.";

/** Meta for the Sernia home page, which only a Sernia host serves at /. */
export function serniaHomeMeta() {
  return [
    { title: SERNIA_HOME_TITLE },
    { name: "description", content: SERNIA_HOME_DESCRIPTION },
    { property: "og:title", content: SERNIA_HOME_TITLE },
    { property: "og:description", content: SERNIA_HOME_DESCRIPTION },
    { property: "og:url", content: serniaUrl("/") },
    { property: "og:type", content: "website" },
    { property: "og:site_name", content: SERNIA_NAME },
    { property: "og:image", content: serniaUrl("/images/sernia/og.png") },
    { property: "og:image:width", content: "1200" },
    { property: "og:image:height", content: "630" },
    { property: "og:image:alt", content: SERNIA_HOME_TITLE },
    { name: "twitter:card", content: "summary_large_image" },
    { tagName: "link", rel: "canonical", href: serniaUrl("/") },
  ];
}

// The photo's column: just over half of the page's column, or all of it on a phone.
const PHOTO_SIZES = "(min-width: 1080px) 522px, (min-width: 768px) 50vw, calc(100vw - 36px)";

const SUBHEADING = "text-[1.35rem] leading-tight font-bold tracking-[-0.01em]";
const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring";
const BUTTON = cn("rounded-lg border-[1.5px] border-foreground text-center leading-[1.2] font-semibold", FOCUS);
const OUTLINE = "hover:bg-foreground/5";
// The sky line is decoration: the text itself stays dark, since sky on white is too light to read.
const TEXT_LINK = cn(
  "rounded-sm font-semibold underline decoration-sernia-sky decoration-2 underline-offset-4 hover:decoration-foreground",
  FOCUS,
);

/** serniacapital.com's home page. Residents sign in from the header; everyone else books a showing or applies. */
export function SerniaHome({ data }: { data: SerniaHomeData }) {
  return (
    <SerniaPublicShell
      wide
      action={
        <a href={RESIDENT_PORTAL_URL} className={cn("px-3.5 py-[9px] text-[0.95rem]", BUTTON, OUTLINE)}>
          Resident sign-in
        </a>
      }
    >
      <div className="text-[1.0625rem] leading-[1.6]">
        <section aria-labelledby="home-heading" className="overflow-hidden bg-sernia-band pt-10 sm:pt-16">
          <div className={cn("grid gap-[22px] pb-2", SERNIA_WIDE_COLUMN)}>
            <h1
              id="home-heading"
              className="text-[clamp(2.1rem,1.4rem+3vw,3.3rem)] leading-[1.08] font-bold tracking-[-0.02em] text-balance"
            >
              {HEADING}
            </h1>
            <p className="text-[1.12rem] text-foreground/85">
              We're a local, responsive team renting apartments around Pittsburgh.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link
                to="/showings"
                className={cn(
                  BUTTON,
                  "flex-auto bg-primary px-[22px] py-[13px] text-primary-foreground hover:bg-primary/90 sm:flex-none",
                )}
              >
                Book a showing
              </Link>
              <a href={APPLY_PATH} className={cn(BUTTON, OUTLINE, "flex-auto px-[22px] py-[13px] sm:flex-none")}>
                Apply
              </a>
            </div>
            {data.bookingOpen && (
              <CallLine
                siteKey={data.turnstileSiteKey}
                lead="Questions?"
                tail="."
                className="text-[1.0625rem] leading-[1.6]"
                linkClassName={TEXT_LINK}
              />
            )}
          </div>
          <div className="mt-4 sm:mt-[18px]">
            <Street className="h-[clamp(140px,22vw,290px)]" />
          </div>
        </section>

        <div
          className={cn(
            "mt-9 mb-[72px] grid gap-8 md:mt-14 md:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] md:items-center md:gap-14",
            SERNIA_WIDE_COLUMN,
          )}
        >
          <picture>
            <source
              type="image/webp"
              srcSet="/images/sernia/kitchen-720.webp 720w, /images/sernia/kitchen-1280.webp 1280w"
              sizes={PHOTO_SIZES}
            />
            <img
              src="/images/sernia/kitchen-720.jpg"
              srcSet="/images/sernia/kitchen-720.jpg 720w, /images/sernia/kitchen-1280.jpg 1280w"
              sizes={PHOTO_SIZES}
              width={1280}
              height={960}
              loading="lazy"
              decoding="async"
              alt="A kitchen with an island and stainless steel appliances"
              className="block h-auto w-full rounded-[10px] bg-muted"
            />
          </picture>
          <div className="grid gap-8 md:gap-10">
            <section className="grid gap-2.5">
              <h2 className={SUBHEADING}>What renters say about us</h2>
              <p>Our past and current renters review us on Zillow.</p>
              <p>
                <a href={ZILLOW_REVIEWS_URL} className={TEXT_LINK}>
                  Read our Zillow reviews
                </a>
              </p>
            </section>
            <section className="grid gap-2.5">
              <h2 className={SUBHEADING}>Already live with us?</h2>
              <p>Pay rent and send repair requests in the resident portal.</p>
              <p>
                <a href={RESIDENT_PORTAL_URL} className={TEXT_LINK}>
                  Resident sign-in
                </a>
              </p>
            </section>
          </div>
        </div>
      </div>
    </SerniaPublicShell>
  );
}
