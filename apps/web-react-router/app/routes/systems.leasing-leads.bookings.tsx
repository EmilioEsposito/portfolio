import { AuthGuard } from "~/components/auth-guard";
import { AgentsSection } from "~/components/showing-bookings/AgentsSection";
import { ConnectionsSection } from "~/components/showing-bookings/ConnectionsSection";
import { MessagesSection } from "~/components/showing-bookings/MessagesSection";
import { SendModeSection } from "~/components/showing-bookings/SendModeSection";
import { ShowingsSection } from "~/components/showing-bookings/ShowingsSection";

export const meta = () => [
  { title: "Booking settings | Zillow Leads Agent" },
  { name: "robots", content: "noindex, nofollow" },
];

export default function BookingSettingsPage() {
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
        <SendModeSection />
        <MessagesSection />
        <ConnectionsSection />
        <AgentsSection />
        <ShowingsSection />
      </div>
    </AuthGuard>
  );
}
