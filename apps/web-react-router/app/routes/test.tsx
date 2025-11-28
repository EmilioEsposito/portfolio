import type { Route } from "./+types/test";
import { H1, P } from "~/components/typography";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Test Page - React Router" },
    { name: "description", content: "Simple test page migrated from Next.js" },
  ];
}

export default function TestPage() {
  return (
    <div className="p-4">
      <H1>Test Page</H1>
      <P>
        Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod
        tempor incididunt ut labore et dolore magna aliqua.
      </P>
      <P>
        This page was migrated from Next.js to React Router v7!
      </P>
    </div>
  );
}
