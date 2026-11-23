import { useEffect, useState, type RefObject } from "react";

export function useIsStandalonePwa(): boolean {
  const [standalone, setStandalone] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia("(display-mode: standalone)");
    const nav = window.navigator as Navigator & { standalone?: boolean };
    const update = () => setStandalone(mql.matches || nav.standalone === true);
    update();
    mql.addEventListener?.("change", update);
    return () => mql.removeEventListener?.("change", update);
  }, []);
  return standalone;
}

interface PullToRefreshOptions {
  scrollContainerRef: RefObject<HTMLElement | null>;
  enabled: boolean;
  threshold?: number;
  onRefresh: () => void;
}

/**
 * iOS standalone PWAs have no native pull-to-refresh. This hook replicates it
 * by watching touch gestures on the scroll container: when the user drags down
 * while scrolled to the top past `threshold`, we fire `onRefresh`.
 * Returns the current pull distance (0 when idle) so the caller can render a
 * progress indicator.
 */
export function usePullToRefresh({
  scrollContainerRef,
  enabled,
  threshold = 80,
  onRefresh,
}: PullToRefreshOptions): number {
  const [pull, setPull] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    const container = scrollContainerRef.current;
    if (!container) return;

    let startY: number | null = null;
    let currentPull = 0;

    const maxPull = threshold * 1.5;
    const resistance = 0.5;

    const onTouchStart = (e: TouchEvent) => {
      if (container.scrollTop <= 0) {
        startY = e.touches[0].clientY;
        currentPull = 0;
      } else {
        startY = null;
      }
    };

    const onTouchMove = (e: TouchEvent) => {
      if (startY === null) return;
      const dy = e.touches[0].clientY - startY;
      if (dy <= 0 || container.scrollTop > 0) {
        startY = null;
        currentPull = 0;
        setPull(0);
        return;
      }
      currentPull = Math.min(dy * resistance, maxPull);
      setPull(currentPull);
    };

    const onTouchEnd = () => {
      if (currentPull >= threshold) {
        onRefresh();
      }
      startY = null;
      currentPull = 0;
      setPull(0);
    };

    container.addEventListener("touchstart", onTouchStart, { passive: true });
    container.addEventListener("touchmove", onTouchMove, { passive: true });
    container.addEventListener("touchend", onTouchEnd);
    container.addEventListener("touchcancel", onTouchEnd);

    return () => {
      container.removeEventListener("touchstart", onTouchStart);
      container.removeEventListener("touchmove", onTouchMove);
      container.removeEventListener("touchend", onTouchEnd);
      container.removeEventListener("touchcancel", onTouchEnd);
    };
  }, [enabled, scrollContainerRef, threshold, onRefresh]);

  return pull;
}
