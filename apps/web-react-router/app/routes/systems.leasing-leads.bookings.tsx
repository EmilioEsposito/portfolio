import { useReducer, useState } from "react";

import type { Route } from "./+types/systems.leasing-leads.bookings";
import { AuthGuard } from "~/components/auth-guard";
import { AgentsSection } from "~/components/showing-bookings/AgentsSection";
import { BookingControls } from "~/components/showing-bookings/BookingControls";
import { CapturedSection } from "~/components/showing-bookings/CapturedSection";
import { ConnectionsSection } from "~/components/showing-bookings/ConnectionsSection";
import { MessagesSection } from "~/components/showing-bookings/MessagesSection";
import { ShowingsSection } from "~/components/showing-bookings/ShowingsSection";
import { serniaPublicPagesEnabled } from "~/lib/sernia-public";
import type { SettingsView } from "~/lib/showing-bookings";

export const meta = () => [
  { title: "Booking settings | Zillow Leads Agent" },
  { name: "robots", content: "noindex, nofollow" },
];

// Whether this environment serves the public booking pages is the web app's own
// switch (SERNIA_PUBLIC_PAGES_ENABLED), which the API does not know.
export function loader() {
  return { publicPagesOn: serniaPublicPagesEnabled() };
}

export default function BookingSettingsPage({ loaderData }: Route.ComponentProps) {
  // The saved settings, which the sections below the controls read too.
  const [settings, setSettings] = useState<SettingsView | null>(null);
  // Bumped when something elsewhere on the tab changed one of the two lists of messages,
  // so it is read again.
  const [capturedChanges, capturedChanged] = useReducer((count: number) => count + 1, 0);
  const [messageChanges, messagesChanged] = useReducer((count: number) => count + 1, 0);
  return (
    <AuthGuard requireDomain="serniacapital.com" message="Sernia sign-in required">
      <div className="max-w-3xl space-y-6">
        <div>
          <h2 className="text-2xl font-semibold">Booking settings</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            Showing bookings are being built step by step. Showing windows, blackout dates and booking links are under
            Rental listing data.{" "}
            <a href="https://github.com/EmilioEsposito/portfolio/issues/368" className="underline underline-offset-4">
              Follow the booking work
            </a>
            .
          </p>
        </div>
        <BookingControls pagesOn={loaderData.publicPagesOn} view={settings} onView={setSettings} />
        <CapturedSection view={settings} changes={capturedChanges} onChanged={messagesChanged} />
        <MessagesSection
          isProduction={settings ? settings.is_production : null}
          changes={messageChanges}
          onChanged={capturedChanged}
        />
        <ConnectionsSection />
        <AgentsSection />
        <ShowingsSection
          settings={settings}
          onChanged={() => {
            capturedChanged();
            messagesChanged();
          }}
        />
      </div>
    </AuthGuard>
  );
}
