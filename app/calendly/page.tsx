"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

export default function CalendlyPage() {
  const { theme } = useTheme();
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

  // Define theme-based colors for Calendly
  const calendlyColors = {
    background_color: theme === 'dark' ? '000000' : 'ffffff',
    text_color: theme === 'dark' ? 'ffffff' : '000000',
    primary_color: '3182ce' // Keep the primary color consistent for brand identity
  };

  const calendlyUrl = `https://calendly.com/emilio_esposito/chat?background_color=${calendlyColors.background_color}&text_color=${calendlyColors.text_color}&primary_color=${calendlyColors.primary_color}`;

  return (
    <div className="w-full min-h-screen bg-background">
      <div 
        className="calendly-inline-widget w-full h-[700px]" 
        data-url={calendlyUrl}
      />
    </div>
  );
} 