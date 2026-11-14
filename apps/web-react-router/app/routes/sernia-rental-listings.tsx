import { redirect } from "react-router";

export function loader() {
  return redirect("/systems/leasing-leads/listings", 301);
}
