import { useEffect } from "react";

/**
 * Tracks window.visualViewport, the part of the page on screen, into CSS
 * variables so the chat (`.chat-shell` in app.css) can cover exactly that
 * part. The iOS keyboard shrinks it and also slides it down the page to keep
 * the focused box in view; a chat pinned to the top of the page slides up
 * with it and leaves a blank band above the message box.
 *   --chat-vh            : height of the visible part
 *   --chat-vv-top        : how far down the page the visible part starts
 *   --chat-keyboard-open : 1 while the on-screen keyboard is up, else 0
 *
 * Also locks html/body scrolling while mounted, since the chat scrolls its
 * own message list.
 */
export function useVisualViewportHeight() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    const vv = window.visualViewport;
    const root = document.documentElement;
    const body = document.body;

    const update = () => {
      const height = vv?.height ?? window.innerHeight;
      root.style.setProperty("--chat-vh", `${height}px`);
      root.style.setProperty("--chat-vv-top", `${vv?.offsetTop ?? 0}px`);
      // A keyboard takes far more than the browser's own bars ever do.
      const keyboardOpen = window.innerHeight - height > 120;
      root.style.setProperty("--chat-keyboard-open", keyboardOpen ? "1" : "0");
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
      root.style.removeProperty("--chat-vv-top");
      root.style.removeProperty("--chat-keyboard-open");
      root.style.overflow = prevHtmlOverflow;
      body.style.overflow = prevBodyOverflow;
      body.style.overscrollBehavior = prevBodyOverscroll;
    };
  }, []);
}
