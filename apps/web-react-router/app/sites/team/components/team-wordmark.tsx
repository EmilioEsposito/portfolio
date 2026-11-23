import { Link } from "react-router";

import { LogoMark } from "~/components/sernia-logo";
import { cn } from "~/lib/utils";

/** The Sernia logo and name. A link to the team home when `linked`. */
export function TeamWordmark({ linked = false, className }: { linked?: boolean; className?: string }) {
  const content = (
    <>
      <LogoMark className="size-7 shrink-0" />
      <span className="truncate">Sernia Capital</span>
    </>
  );
  const style = cn("sernia-brand flex min-w-0 items-center gap-2 font-semibold", className);
  if (!linked) return <span className={style}>{content}</span>;
  return (
    <Link
      to="/team"
      className={cn(style, "rounded-sm hover:opacity-80 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring")}
    >
      {content}
    </Link>
  );
}
