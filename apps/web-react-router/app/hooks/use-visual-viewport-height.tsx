import { useEffect } from "react";

/**
 * Tracks window.visualViewport.height into the CSS variable --chat-vh so
 * flex containers can stay within the visible viewport when the iOS virtual
 * keyboard opens. dvh alone does not shrink for the keyboard on iOS Safari.
 */
export function useVisualViewportHeight() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    const root = document.documentElement;

    const update = () => {
      const h = vv ? vv.height : window.innerHeight;
      root.style.setProperty("--chat-vh", `${h}px`);
    };

    update();

    if (vv) {
      vv.addEventListener("resize", update);
      vv.addEventListener("scroll", update);
    }
    window.addEventListener("resize", update);
    window.addEventListener("orientationchange", update);

    return () => {
      if (vv) {
        vv.removeEventListener("resize", update);
        vv.removeEventListener("scroll", update);
      }
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
      root.style.removeProperty("--chat-vh");
    };
  }, []);
}
