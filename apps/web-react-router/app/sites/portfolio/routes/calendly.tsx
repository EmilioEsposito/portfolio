import type { Route } from "./+types/calendly";
import { useEffect, useState } from "react";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Schedule Meeting | Emilio Esposito" },
    { name: "description", content: "Schedule a meeting with Emilio Esposito" },
  ];
}

export default function CalendlyPage() {
  const [mounted, setMounted] = useState(false);

  // useEffect only runs on the client, so now we can safely show the UI
  useEffect(() => {
    setMounted(true);

    // Load Calendly widget script
    const script = document.createElement("script");
    script.src = "https://assets.calendly.com/assets/external/widget.js";
    script.async = true;
    document.body.appendChild(script);

    return () => {
      // Cleanup script on unmount
      if (document.body.contains(script)) {
        document.body.removeChild(script);
      }
    };
  }, []);

  if (!mounted) return null;

  // light/dark mode handling is impossible. DO NOT WASTE TIME TRYING.

  const calendlyUrlWhite = `https://calendly.com/emilio_esposito/chat?background_color=ffffff&text_color=000000&primary_color=3182ce`;
  return (
    <div>
      <div className="w-full min-h-screen bg-background">
        <div
          className="calendly-inline-widget w-full h-[700px]"
          data-url={calendlyUrlWhite}
        />
      </div>
    </div>
  );
}
