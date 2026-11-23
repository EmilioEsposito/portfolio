/**
 * The units on offer, as the list at /showings and the Sernia home page read
 * them from `GET /api/showing-bookings/public/units`. Kept apart from React so
 * it can be tested.
 */
import { monthlyRent } from "~/sites/sernia-public/lib/showing-booking-page";

/** One unit on offer. `lease_start` is worded by the API, by the same rule as the booking form's tick. */
export type UnitOnOffer = {
  unit: string;
  address: string;
  rent: number | null;
  lease_start_earliest: string | null;
  lease_start_latest: string | null;
  /** "between Jun 1 and Jul 1, 2027", "on Jun 1, 2027", or null. */
  lease_start: string | null;
  has_open_times: boolean;
};

/** `GET /units`. */
export type UnitsOnOffer = { units: UnitOnOffer[]; turnstile_site_key: string | null };

/**
 * What a page knows about the list:
 * - `open`: public booking is on; the units, possibly none.
 * - `closed`: public booking is off (the API answers 404), so there is no list and no call line.
 * - `unavailable`: the API could not be read.
 */
export type UnitsState =
  | { status: "open"; units: UnitOnOffer[]; turnstileSiteKey: string | null }
  | { status: "closed" }
  | { status: "unavailable" };

/** The booking page of a unit. */
export function unitPath(unit: string): string {
  return `/showings/${encodeURIComponent(unit)}`;
}

/** The address, then "unit 320-03" after it; the unit alone with no address. */
export function unitHeading(item: Pick<UnitOnOffer, "unit" | "address">): { title: string; detail: string | null } {
  return item.address ? { title: item.address, detail: `unit ${item.unit}` } : { title: `Unit ${item.unit}`, detail: null };
}

/** "$1,450 a month · Lease starts between Jun 1 and Jul 1, 2027", leaving out what the listing lacks. */
export function unitTerms(item: Pick<UnitOnOffer, "rent" | "lease_start">): string {
  const items: string[] = [];
  if (item.rent !== null) items.push(monthlyRent(item.rent));
  if (item.lease_start) items.push(`Lease starts ${item.lease_start}`);
  return items.join(" · ");
}
