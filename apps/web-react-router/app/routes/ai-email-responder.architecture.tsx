import { redirect } from "react-router";

// Retired fictional email studio; keep existing links useful.
export function loader() {
  return redirect("/systems/leasing-leads", 301);
}
