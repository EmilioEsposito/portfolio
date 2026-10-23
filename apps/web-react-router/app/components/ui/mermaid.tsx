"use client";

import mermaid from "mermaid";
import { useEffect, useRef } from "react";
import { useTheme } from "next-themes";

mermaid.initialize({
  // React owns the initial DOM; automatic rendering can race hydration.
  startOnLoad: false,
  theme: 'default',
  securityLevel: 'loose',
  fontFamily: 'inherit',
});

interface MermaidProps {
  chart: string;
}

export default function Mermaid({ chart }: MermaidProps) {
  const { theme } = useTheme();
  const mermaidRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (mermaidRef.current) {
      mermaid.initialize({
        theme: theme === 'dark' ? 'dark' : 'default',
        fontFamily: 'inherit',
      });
      void mermaid.run({ nodes: [mermaidRef.current] });
    }
  }, [theme, chart]);

  return (
    <div className="mermaid w-full overflow-x-auto" ref={mermaidRef}>
      {chart}
    </div>
  );
}
