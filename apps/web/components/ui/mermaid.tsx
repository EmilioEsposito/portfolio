"use client";

import mermaid from "mermaid";
import { useEffect, useRef } from "react";
import { useTheme } from "next-themes";

mermaid.initialize({
  startOnLoad: true,
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
      mermaid.contentLoaded();
    }
  }, [theme, chart]);

  return (
    <div className="mermaid w-full overflow-x-auto" ref={mermaidRef}>
      {chart}
    </div>
  );
} 