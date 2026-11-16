import { useCallback, useEffect, useRef } from "react";

/**
 * Cloudflare Turnstile in managed mode, run on demand. Each call to the
 * hook's `token()` runs the check and resolves with a fresh token, because a
 * token works once. Most browsers see nothing; the widget's container shows
 * a challenge only when Turnstile asks for one (`appearance: interaction-only`).
 *
 * The script loads from Cloudflare only when a check first runs. The browser
 * test answers that script itself, so nothing reaches Cloudflare there.
 */

type TurnstileOptions = {
  sitekey: string;
  execution: "execute";
  appearance: "interaction-only";
  callback: (token: string) => void;
  "error-callback": () => boolean;
  "expired-callback": () => void;
  "timeout-callback": () => void;
};

type TurnstileApi = {
  render(container: HTMLElement, options: TurnstileOptions): string | undefined;
  execute(widgetId: string): void;
  reset(widgetId: string): void;
  remove(widgetId: string): void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

export const TURNSTILE_SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

export class TurnstileError extends Error {}

let loading: Promise<TurnstileApi> | null = null;

function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise<TurnstileApi>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = TURNSTILE_SCRIPT;
    script.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new TurnstileError("not loaded")));
    script.onerror = () => {
      // Let a later attempt try again, such as after the connection comes back.
      loading = null;
      script.remove();
      reject(new TurnstileError("could not load"));
    };
    document.head.appendChild(script);
  });
  return loading;
}

type Pending = { resolve: (token: string) => void; reject: (error: Error) => void };

export function useTurnstile(siteKey: string | null) {
  const container = useRef<HTMLDivElement>(null);
  const widget = useRef<string | undefined>(undefined);
  const pending = useRef<Pending | null>(null);

  useEffect(
    () => () => {
      if (widget.current !== undefined) window.turnstile?.remove(widget.current);
      widget.current = undefined;
    },
    [],
  );

  const token = useCallback(async (): Promise<string> => {
    if (!siteKey) throw new TurnstileError("no site key");
    const api = await loadTurnstile();
    const element = container.current;
    if (!element) throw new TurnstileError("no container");
    pending.current?.reject(new TurnstileError("superseded"));
    return new Promise<string>((resolve, reject) => {
      pending.current = { resolve, reject };
      const settle = (value: string | null) => {
        const waiting = pending.current;
        pending.current = null;
        if (!waiting) return;
        if (value) waiting.resolve(value);
        else waiting.reject(new TurnstileError("check failed"));
      };
      try {
        if (widget.current === undefined) {
          widget.current = api.render(element, {
            sitekey: siteKey,
            execution: "execute",
            appearance: "interaction-only",
            callback: (value) => settle(value),
            "error-callback": () => {
              settle(null);
              return true;
            },
            "expired-callback": () => settle(null),
            "timeout-callback": () => settle(null),
          });
        } else {
          api.reset(widget.current);
        }
        if (widget.current === undefined) throw new TurnstileError("not rendered");
        api.execute(widget.current);
      } catch (error) {
        settle(null);
        if (!(error instanceof TurnstileError)) console.error("Turnstile", error);
      }
    });
  }, [siteKey]);

  return { container, token };
}
