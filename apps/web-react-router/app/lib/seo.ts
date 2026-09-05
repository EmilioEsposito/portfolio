// SEO & AI SEO (GEO) utilities for meta tags, JSON-LD, and canonical links

export const SITE_URL = "https://eesposito.com";
export const SITE_OWNER = "Emilio Esposito";
export const DEFAULT_META = {
  title: "Emilio Esposito | AI Engineering & Enablement",
  description:
    "Emilio Esposito is an AI engineering leader and hands-on engineer at LegalZoom, building production AI systems and enabling a 200-person engineering organization.",
  image: `${SITE_URL}/images/me_emilio_headshot_2026_square.jpg`,
};

export function buildUrl(path: string): string {
  return `${SITE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

interface OgMetaConfig {
  title: string;
  description: string;
  url: string;
  image?: string;
  type?: string;
}

/**
 * Returns an array of Open Graph + Twitter Card meta descriptors
 * to spread into a React Router `meta()` return.
 */
export function generateOgMeta(config: OgMetaConfig) {
  const image = config.image ?? DEFAULT_META.image;
  return [
    { property: "og:title", content: config.title },
    { property: "og:description", content: config.description },
    { property: "og:url", content: config.url },
    { property: "og:image", content: image },
    { property: "og:type", content: config.type ?? "website" },
    { property: "og:site_name", content: SITE_OWNER },
    { name: "twitter:card", content: "summary_large_image" },
    { name: "twitter:title", content: config.title },
    { name: "twitter:description", content: config.description },
    { name: "twitter:image", content: image },
  ];
}

/**
 * Returns a `{ "script:ld+json": schema }` descriptor for React Router's meta function.
 */
export function generateJsonLd(schema: Record<string, unknown>) {
  // React Router serializes and escapes this object when rendering <Meta />.
  // Pre-stringifying it produces a JSON string instead of crawlable structured data.
  return { "script:ld+json": schema };
}

/**
 * Returns a canonical link descriptor for React Router's `links()` export.
 */
export function generateCanonicalLink(url: string) {
  return { rel: "canonical", href: url } as const;
}
