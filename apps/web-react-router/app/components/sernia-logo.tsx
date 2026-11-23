/**
 * Sernia Capital's logo mark, for the Sernia public pages and the team's
 * pages. It takes its colors from `.sernia-public` or `.sernia-brand` in
 * app.css, so it follows the theme, and it is decoration: hidden from screen
 * readers.
 */

/** The mark, without the name. `public/images/sernia/icon.svg` is the same drawing. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 743 743" aria-hidden="true" className={className}>
      <polygon
        className="fill-sernia-ink"
        points="338,58 11,393 136,393 136,643 281,643 237,689 269,720 366,620 269,522 237,553 281,598 182,598 182,348 119,348 369,90"
      />
      <polygon
        className="fill-sernia-sky"
        points="369,28 732,389 606,389 606,643 404,643 404,393 296,393 296,348 449,348 449,598 561,598 561,345 623,345 338,59"
      />
    </svg>
  );
}
