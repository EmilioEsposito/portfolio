import { useEffect } from "react";

/**
 * Tracks window.visualViewport into CSS variables so the chat input bar
 * can stay anchored to the bottom of the visible viewport, above the iOS
 * keyboard:
 *   --chat-vh        : visualViewport.height (height of visible viewport)
 *   --keyboard-inset : space the keyboard takes from the bottom of the
 *                      layout viewport (0 when keyboard is closed)
 *
 * Also locks html/body scrolling while mounted so iOS Safari can't push
 * the page up when the keyboard opens.
 */
export function useVisualViewportHeight() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    const root = document.documentElement;
    const body = document.body;

    const update = () => {
      if (vv) {
        root.style.setProperty("--chat-vh", `${vv.height}px`);
        const inset = Math.max(
          0,
          window.innerHeight - vv.height - vv.offsetTop
        );
        root.style.setProperty("--keyboard-inset", `${inset}px`);
      } else {
        root.style.setProperty("--chat-vh", `${window.innerHeight}px`);
        root.style.setProperty("--keyboard-inset", "0px");
      }
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
      root.style.removeProperty("--keyboard-inset");
      root.style.overflow = prevHtmlOverflow;
      body.style.overflow = prevBodyOverflow;
      body.style.overscrollBehavior = prevBodyOverscroll;
    };
  }, []);
}
