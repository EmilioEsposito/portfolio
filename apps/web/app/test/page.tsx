"use client";

import { DatePicker } from "@/components/ui/date-picker";
import { P, H1 } from "@/components/typography";

export default function Page() {
  return (
    <div className="p-4">
      <H1>Test Page</H1>
      <P>
        Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod
        tempor incididunt ut labore et dolore magna aliqua.
      </P>
      <P>
        <DatePicker />
      </P>
    </div>
  );
}
