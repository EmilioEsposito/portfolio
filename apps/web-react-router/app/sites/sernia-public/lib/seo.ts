// Names, addresses and meta tags for Sernia Capital's public pages. These
// pages never use the personal site's SEO helpers in ~/sites/portfolio/lib/seo.

export const SERNIA_NAME = "Sernia Capital";
export const SERNIA_SITE_URL = "https://serniacapital.com";
export const SERNIA_CONTACT_EMAIL = "all@serniacapital.com";

export function serniaUrl(path: string): string {
  return `${SERNIA_SITE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

/**
 * Title, description and Open Graph tags for a Sernia public page. The
 * canonical link points at serniacapital.com, because the same paths also
 * answer on other hosts (previews, localhost and eesposito.com).
 */
export function serniaPageMeta(config: { title: string; description: string; path: string }) {
  const title = `${config.title} | ${SERNIA_NAME}`;
  const url = serniaUrl(config.path);
  return [
    { title },
    { name: "description", content: config.description },
    { property: "og:title", content: title },
    { property: "og:description", content: config.description },
    { property: "og:url", content: url },
    { property: "og:type", content: "website" },
    { property: "og:site_name", content: SERNIA_NAME },
    { tagName: "link", rel: "canonical", href: url },
  ];
}

/** Meta for an error page shown under the Sernia layout. */
export function serniaErrorMeta(status: number | undefined) {
  return [
    { title: `${status === 404 ? "Page not found" : "Something went wrong"} | ${SERNIA_NAME}` },
    { name: "robots", content: "noindex" },
  ];
}
