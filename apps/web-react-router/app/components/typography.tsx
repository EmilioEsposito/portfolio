import { cn } from "~/lib/utils";

export function H1({ className, children }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h1 className={cn(
      "scroll-m-20 text-4xl font-extrabold tracking-tight lg:text-5xl",
      className
    )}>
      {children}
    </h1>
  );
}

export function H2({ className, children }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2
      className={cn(
        "mt-10 scroll-m-20 border-b pb-2 text-3xl font-semibold tracking-tight transition-colors first:mt-0",
        className
      )}
    >
      {children}
    </h2>
  );
}

export function H3({ className, children }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn(
        "scroll-m-20 text-2xl font-semibold tracking-tight -mb-2",
        className
      )}
    >
      {children}
    </h3>
  );
}

export function P({ className, children }: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p
      className={cn(
        "leading-7 mb-2",
        className
      )}
    >
      {children}
    </p>
  );
}

export function Lead({ className, children }: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p
      className={cn(
        "text-xl text-muted-foreground",
        className
      )}
    >
      {children}
    </p>
  );
}

export function Large({ className, children }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "text-lg font-semibold",
        className
      )}
    >
      {children}
    </div>
  );
}

export function Small({ className, children }: React.HTMLAttributes<HTMLElement>) {
  return (
    <small
      className={cn(
        "text-sm font-medium leading-none",
        className
      )}
    >
      {children}
    </small>
  );
}

export function Muted({ className, children }: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p
      className={cn(
        "text-sm text-muted-foreground",
        className
      )}
    >
      {children}
    </p>
  );
}
