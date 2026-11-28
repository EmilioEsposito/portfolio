import { Link } from "react-router";

import { LeasingOverview } from "~/components/leasing-overview";
import { teamTitle } from "~/sites/team/lib/team-meta";

export const meta = () => [{ title: teamTitle("How the lead agent works") }];

// How the lead agent works, for the team: the same overview the portfolio's
// write-up shows (~/components/leasing-overview), under the team's own
// introduction and links.

export default function LeadAgentOverview() {
  return (
    <>
      <p className="mb-7 max-w-3xl text-base leading-7 text-muted-foreground">
        How Zillow leads become replies and showings: what the agent reads and sends, who is told about
        each showing and when, and what is left to the team.
      </p>
      <LeasingOverview />
      <footer className="mt-9 flex flex-wrap gap-x-6 gap-y-3 border-t pt-5 text-sm">
        <Link to="/team/leads" className="underline underline-offset-4">Leads</Link>
        <Link to="/team/leads/listings" className="underline underline-offset-4">Rental listing data</Link>
        <Link to="/team/leads/settings" className="underline underline-offset-4">Agent settings</Link>
        <Link to="/team/leads/bookings" className="underline underline-offset-4">Booking settings</Link>
        <Link to="/team/chat" className="underline underline-offset-4">Agent conversations</Link>
      </footer>
    </>
  );
}
