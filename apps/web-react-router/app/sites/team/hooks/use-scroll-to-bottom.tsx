import { useEffect, useRef, type RefObject } from "react";

export function useScrollToBottom<T extends HTMLElement>(): [
  RefObject<T | null>,
  RefObject<T | null>
] {
  const containerRef = useRef<T>(null);
  const endRef = useRef<T>(null);

  useEffect(() => {
    const container = containerRef.current;
    const end = endRef.current;

    if (container && end) {
      // Start at the bottom when mounting with existing history — the
      // MutationObserver below only reacts to later changes.
      if (container.scrollHeight > container.clientHeight) {
        end.scrollIntoView({ behavior: "auto", block: "end" });
      }

      const observer = new MutationObserver((mutations) => {
        // Ignore mutations from form/textarea elements (e.g. textarea auto-resize
        // changes style.height, which shouldn't trigger auto-scroll)
        const isFromInput = mutations.every((m) => {
          const target = m.target as HTMLElement;
          return target.closest?.("form") != null;
        });
        if (isFromInput) return;

        // Only auto-scroll if user is already near the bottom.
        // This prevents jumping when expanding tool cards, etc.
        const threshold = 80;
        const distanceFromBottom =
          container.scrollHeight -
          container.scrollTop -
          container.clientHeight;

        if (distanceFromBottom < threshold) {
          end.scrollIntoView({ behavior: "auto", block: "end" });
        }
      });

      observer.observe(container, {
        childList: true,
        subtree: true,
      });

      // When the list gets shorter (the phone keyboard opening), keep the
      // latest message above the message box if it was in view.
      const nearBottom = () =>
        container.scrollHeight - container.scrollTop - container.clientHeight <
        80;
      let wasNearBottom = nearBottom();
      const onScroll = () => {
        wasNearBottom = nearBottom();
      };
      container.addEventListener("scroll", onScroll, { passive: true });
      const resizeObserver =
        typeof ResizeObserver === "undefined"
          ? null
          : new ResizeObserver(() => {
              if (wasNearBottom) container.scrollTop = container.scrollHeight;
            });
      resizeObserver?.observe(container);

      return () => {
        observer.disconnect();
        container.removeEventListener("scroll", onScroll);
        resizeObserver?.disconnect();
      };
    }
  }, []);

  return [containerRef, endRef];
}
