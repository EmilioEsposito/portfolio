// Titles of the team's pages. The root adds `noindex` to every team page, so
// a page's meta needs only its title and, if it likes, a description.

const TEAM_NAME = "Sernia Capital team";

/** "Page | Sernia Capital team", or the team's name alone for the team home. */
export function teamTitle(page?: string): string {
  return page ? `${page} | ${TEAM_NAME}` : TEAM_NAME;
}
