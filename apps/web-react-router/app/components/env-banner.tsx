import { useState, useEffect } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";

type EnvInfo = {
  name: string;
  color: string;
};

const ENVS: Record<string, { label: string; url: string }> = {
  prod: { label: "Production", url: "https://eesposito.com" },
  dev: { label: "Dev", url: "https://dev.eesposito.com" },
};

function detectEnv(hostname: string): EnvInfo | null {
  if (hostname === "eesposito.com" || hostname === "www.eesposito.com") {
    return null; // production — no banner
  }
  if (hostname === "dev.eesposito.com") {
    return { name: "DEV", color: "bg-yellow-500 text-yellow-950" };
  }
  if (hostname === "localhost" || hostname === "127.0.0.1") {
    return { name: "LOCAL", color: "bg-blue-500 text-white" };
  }
  // PR preview or other environments
  return { name: "PR", color: "bg-purple-500 text-white" };
}

export function EnvBanner() {
  const [env, setEnv] = useState<EnvInfo | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setEnv(detectEnv(window.location.hostname));
  }, []);

  if (!mounted || !env) return null;

  return (
    <div
      className={`flex items-center justify-center gap-2 px-3 py-1 text-xs font-medium ${env.color}`}
    >
      <span>{env.name}</span>
      <Select
        value=""
        onValueChange={(value) => {
          const target = ENVS[value];
          if (target) window.location.href = target.url;
        }}
      >
        <SelectTrigger className="h-5 w-auto gap-1 border-none bg-transparent px-1.5 text-xs font-medium shadow-none focus:ring-0 [&>svg]:h-3 [&>svg]:w-3">
          <SelectValue placeholder="Switch" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="prod">Production</SelectItem>
          <SelectItem value="dev">Dev</SelectItem>
        </SelectContent>
      </Select>
    </div>
  );
}
