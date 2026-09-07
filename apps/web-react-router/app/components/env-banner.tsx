import { useEffect, useRef } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import {
  type DeploymentEnvironment,
  getDeploymentEnvironment,
} from "~/lib/deployment-env";

type EnvInfo = {
  name: string;
  color: string;
};

const ENVS: Record<string, { label: string; origin: string }> = {
  prod: { label: "Production", origin: "https://eesposito.com" },
  dev: { label: "Dev", origin: "https://dev.eesposito.com" },
  local: { label: "Localhost", origin: "http://localhost:5173" },
};

function getEnvInfo(environment: DeploymentEnvironment): EnvInfo | null {
  switch (environment) {
    case "production":
      return null;
    case "development":
      return { name: "DEV", color: "bg-yellow-500 text-yellow-950" };
    case "local":
      return { name: "LOCAL", color: "bg-blue-500 text-white" };
    case "preview":
      return { name: "PR", color: "bg-purple-500 text-white" };
  }
}

export function EnvBanner({ railwayEnvironmentName }: { railwayEnvironmentName?: string }) {
  const env = getEnvInfo(getDeploymentEnvironment(railwayEnvironmentName));
  const ref = useRef<HTMLDivElement>(null);

  // Expose the banner's rendered height as a CSS var so viewport-height
  // layouts (chat, admin, settings) can subtract it and avoid clipping.
  useEffect(() => {
    const el = ref.current;
    const root = document.documentElement;
    if (!el || !env) {
      root.style.setProperty("--env-banner-h", "0px");
      return;
    }
    const setHeight = () => {
      root.style.setProperty("--env-banner-h", `${el.offsetHeight}px`);
    };
    setHeight();
    const ro = new ResizeObserver(setHeight);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.setProperty("--env-banner-h", "0px");
    };
  }, [env]);

  if (!env) return null;

  return (
    <div
      ref={ref}
      className={`flex items-center justify-center gap-2 px-3 py-1 text-xs font-medium ${env.color}`}
    >
      <span>{env.name}</span>
      <Select
        value=""
        onValueChange={(value) => {
          const target = ENVS[value];
          if (!target) return;
          // Preserve current path/query/hash so switching envs keeps you on
          // the same page instead of bouncing to the homepage.
          const suffix =
            window.location.pathname + window.location.search + window.location.hash;
          window.location.href = target.origin + suffix;
        }}
      >
        <SelectTrigger className="h-5 w-auto gap-1 border-none bg-transparent px-1.5 text-xs font-medium shadow-none focus:ring-0 [&>svg]:h-3 [&>svg]:w-3">
          <SelectValue placeholder="Switch" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="prod">Production</SelectItem>
          <SelectItem value="dev">Dev</SelectItem>
          <SelectItem value="local">Localhost</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}
