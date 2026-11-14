import { useUser } from "@clerk/react-router";
import { Link, NavLink, Outlet } from "react-router";
import { cn } from "~/lib/utils";

const tabs = [
  { to: "/systems/leasing-leads", label: "Architecture", public: true },
  { to: "/systems/leasing-leads/listings", label: "Rental listing data" },
  { to: "/systems/leasing-leads/settings", label: "Agent settings" },
  { to: "/systems/leasing-leads/bookings", label: "Booking settings", upcoming: true },
];

export default function ZillowLeadsLayout() {
  const { user } = useUser();
  const isSernia = user?.emailAddresses?.some(
    (email) => email.emailAddress.endsWith("@serniacapital.com") && email.verification?.status === "verified",
  );
  return (
    <main className="mx-auto w-full max-w-7xl px-5 py-9 sm:px-8 sm:py-12">
      <Link to="/sernia-capital" className="text-sm text-muted-foreground underline underline-offset-4">Sernia Capital systems</Link>
      <header className="mb-6 mt-6">
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">Zillow Leads Agent</h1>
      </header>
      <nav aria-label="Zillow Leads Agent pages" className="mb-7 flex flex-wrap gap-x-5 gap-y-1 border-b">
        {tabs.filter((tab) => tab.public || isSernia).map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to}
            end
            className={({ isActive }) => cn(
              "flex shrink-0 items-center gap-2 border-b-2 px-1 pb-3 pt-1 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
              isActive ? "border-foreground text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
            {tab.upcoming && <span className="rounded border border-dashed px-1.5 py-0.5 text-[10px] font-normal">In progress</span>}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </main>
  );
}
