import type { Route } from "./+types/home";
import { PersonalHome, personalHomeMeta } from "~/sites/portfolio/components/home";
import { SerniaHome, serniaHomeMeta, type SerniaHomeData } from "~/sites/sernia-public/components/home";
import { readCallLine, readListAhead } from "~/sites/sernia-public/lib/showing-units.server";
import { isSerniaHomeRequest } from "~/sites/site";

// `/` is the one page that depends on the host. On a Sernia host, while the
// Sernia pages are on, it is Sernia Capital's home page, served without Clerk
// like the other Sernia pages (~/sites/site decides). Everywhere else it is
// the personal site's home page, as before.

export async function loader({ url, request }: Route.LoaderArgs): Promise<{ serniaHome: SerniaHomeData | null }> {
  if (!isSerniaHomeRequest(url)) return { serniaHome: null };
  // Residents type the domain to reach the portal, so this page never waits on
  // the units list: the call line's own route answers at once, or the page
  // renders without the call line after a short timeout. The list is read
  // ahead behind it, for the visitor who clicks Book a showing.
  const callLine = await readCallLine();
  if (callLine.bookingOpen) readListAhead(request);
  return { serniaHome: callLine };
}

export function meta({ loaderData }: Route.MetaArgs) {
  if (loaderData?.serniaHome) return serniaHomeMeta();
  return personalHomeMeta();
}

export default function Home({ loaderData }: Route.ComponentProps) {
  if (loaderData.serniaHome) return <SerniaHome data={loaderData.serniaHome} />;
  return <PersonalHome />;
}
