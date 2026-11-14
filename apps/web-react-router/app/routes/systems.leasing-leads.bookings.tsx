import { CalendarDays } from "lucide-react";
import { AuthGuard } from "~/components/auth-guard";

export const meta = () => [
  { title: "Booking settings | Zillow Leads Agent" },
  { name: "robots", content: "noindex, nofollow" },
];

export default function BookingSettingsPage() {
  return (
    <AuthGuard requireDomain="serniacapital.com" message="Sernia sign-in required">
      <section className="max-w-2xl rounded-xl border border-dashed p-6 sm:p-8">
        <CalendarDays className="mb-4 size-7 text-muted-foreground" aria-hidden="true" />
        <h2 className="text-xl font-semibold">Booking settings</h2>
        <p className="mt-2 text-sm font-medium text-muted-foreground">Under construction</p>
        <p className="mt-4 text-sm leading-6 text-muted-foreground">
          Showing agents, shared calendars, and booking coordination will be configured here.
          Showing windows, blackout dates, and booking links are already available under Rental listing data.
        </p>
        <a href="https://github.com/EmilioEsposito/portfolio/issues/368" className="mt-5 inline-block text-sm underline underline-offset-4">Follow the booking work</a>
      </section>
    </AuthGuard>
  );
}
