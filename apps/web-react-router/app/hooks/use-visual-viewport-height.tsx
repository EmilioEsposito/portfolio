import { useEffect } from "react";

/**
 * Tracks window.visualViewport.height into the CSS variable --chat-vh so
 * flex containers can stay within the visible viewport when the iOS virtual
 * keyboard opens. dvh alone does not shrink for the keyboard on iOS Safari.
 *
 * Also locks html/body scrolling while mounted: without this, iOS Safari
 * scrolls the page when the keyboard opens, pushing the chat input below
 * the visible area. With body scroll locked and the chat shell sized to
 * --chat-vh, the input bar stays anchored just above the keyboard.
 */
export function useVisualViewportHeight() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    const root = document.documentElement;
    const body = document.body;

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

    const prevHtmlOverflow = root.style.overflow;
    const prevBodyOverflow = body.style.overflow;
    const prevBodyOverscroll = body.style.overscrollBehavior;
    root.style.overflow = "hidden";
    body.style.overflow = "hidden";
    body.style.overscrollBehavior = "none";

    return () => {
      if (vv) {
        vv.removeEventListener("resize", update);
        vv.removeEventListener("scroll", update);
      }
      window.removeEventListener("resize", update);
      window.removeEventListener("orientationchange", update);
      root.style.removeProperty("--chat-vh");
      root.style.overflow = prevHtmlOverflow;
      body.style.overflow = prevBodyOverflow;
      body.style.overscrollBehavior = prevBodyOverscroll;
    };
  }, []);
}
