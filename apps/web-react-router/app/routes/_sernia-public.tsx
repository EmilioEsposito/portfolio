import { Outlet } from "react-router";

import { SerniaPublicShell } from "~/components/sernia-public/shell";

// Pathless layout for Sernia Capital's public pages. Each page under it must
// sit at a Sernia public path (see ~/lib/sernia-public), so the root serves it
// without Clerk; test/lib/sernia-public.test.ts checks the route files.
export default function SerniaPublicLayout() {
  return (
    <SerniaPublicShell>
      <Outlet />
    </SerniaPublicShell>
  );
}
