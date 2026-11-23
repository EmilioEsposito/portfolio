import { useCallback, useEffect, useRef } from "react";

import { TokenHolder, TurnstileError } from "~/lib/turnstile-tokens";

/**
 * Cloudflare Turnstile in managed mode. Each `token()` from the hook answers
 * with a token for one use, because a token works once.
 *
 * The check takes a few seconds, so it runs ahead of time: once the page is
 * idle after loading, or at the first sign of use if that comes sooner, the
 * script loads and one check runs. Its token is held (~/lib/turnstile-tokens),
 * and the click that needs it gets it at once. The call line and the booking
 * form on a page share the one held token; whoever takes it, the next is got
 * ready. A check ahead of time that fails is silent, and no other runs until
 * a person's own check passes. A token that has expired is replaced when the
 * person next uses the page.
 *
 * Two kinds of check, so that nothing appears before the person acts:
 * - Ahead of time, the widget sits in an element of its own that no one can
 *   see or focus (`backstage`). Most checks need nothing from the person. One
 *   that does calls `before-interactive-callback`; it is dropped there, and the
 *   person's own check asks them when they act.
 * - When the person acts and no token is ready or on its way, the check runs
 *   in the caller's container, beside what they pressed, and shows a challenge
 *   only if Turnstile asks for one (`appearance: interaction-only`).
 *
 * Nothing runs ahead of time with no site key, while the page is hidden, or
 * once nothing on the page needs a token. The widget is removed as soon as it
 * answers, so Turnstile's own retries and refreshes never run; a token outlives
 * its widget.
 *
 * The browser test answers the script itself, so nothing reaches Cloudflare there.
 */

type TurnstileOptions = {
  sitekey: string;
  execution: "execute";
  appearance: "interaction-only";
  callback: (token: string) => void;
  "error-callback": () => boolean;
  "expired-callback": () => void;
  "timeout-callback": () => void;
  "before-interactive-callback"?: () => void;
};

type TurnstileApi = {
  render(container: HTMLElement, options: TurnstileOptions): string | undefined;
  execute(widgetId: string): void;
  remove(widgetId: string): void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

export const TURNSTILE_SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

let loading: Promise<TurnstileApi> | null = null;

function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise<TurnstileApi>((resolve, reject) => {
    const script = document.createElement("script");
    const fail = (message: string) => {
      // Let a later attempt try again, such as after the connection comes back.
      loading = null;
      script.remove();
      reject(new TurnstileError(message));
    };
    script.src = TURNSTILE_SCRIPT;
    script.onload = () => (window.turnstile ? resolve(window.turnstile) : fail("not loaded"));
    script.onerror = () => fail("could not load");
    document.head.appendChild(script);
  });
  return loading;
}

/**
 * One check in `container`, until it answers or `signal` aborts. `attended` is
 * whether a person is waiting: without one, a check that would ask them
 * something is dropped instead.
 */
function runCheck(
  siteKey: string,
  container: HTMLElement | null,
  { attended, signal }: { attended: boolean; signal?: AbortSignal },
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    let api: TurnstileApi | undefined;
    let widget: string | undefined;
    let answered = false;

    function answer(token: string | null, reason = "check failed") {
      if (answered) return;
      answered = true;
      signal?.removeEventListener("abort", onAbort);
      // Not from inside Turnstile's own callback.
      setTimeout(() => {
        if (widget !== undefined) api?.remove(widget);
      });
      if (token) resolve(token);
      else reject(new TurnstileError(reason));
    }
    const onAbort = () => answer(null, "stopped");

    if (signal?.aborted) return answer(null, "stopped");
    signal?.addEventListener("abort", onAbort);

    loadTurnstile().then(
      (loaded) => {
        if (answered) return;
        if (!container) return answer(null, "no container");
        api = loaded;
        try {
          widget = api.render(container, {
            sitekey: siteKey,
            execution: "execute",
            appearance: "interaction-only",
            callback: (token) => answer(token),
            "error-callback": () => {
              answer(null);
              return true;
            },
            "expired-callback": () => answer(null),
            "timeout-callback": () => answer(null),
            ...(attended ? {} : { "before-interactive-callback": () => answer(null, "needs the person") }),
          });
          if (widget === undefined) return answer(null, "not rendered");
          api.execute(widget);
        } catch (error) {
          console.error("Turnstile", error);
          answer(null);
        }
      },
      (error: Error) => answer(null, error.message),
    );
  });
}

/**
 * Where checks run ahead of time. It is laid out in the viewport, so the
 * browser does not slow the widget's frame down, but fully transparent, inert
 * and behind the page: nothing in it can be seen, focused or pressed.
 */
function backstage(): HTMLElement {
  const id = "turnstile-backstage";
  const existing = document.getElementById(id);
  if (existing) return existing;
  const element = document.createElement("div");
  element.id = id;
  element.inert = true;
  element.setAttribute("aria-hidden", "true");
  element.style.cssText =
    "position:fixed;left:0;bottom:0;z-index:-1;width:300px;height:65px;max-width:100%;overflow:hidden;opacity:0;pointer-events:none";
  document.body.appendChild(element);
  return element;
}

function whenIdle(run: () => void) {
  // Safari has no requestIdleCallback.
  if (typeof requestIdleCallback === "function") requestIdleCallback(run, { timeout: 2_000 });
  else setTimeout(run, 500);
}

function afterLoad(run: () => void) {
  if (document.readyState === "complete") whenIdle(run);
  else window.addEventListener("load", () => whenIdle(run), { once: true });
}

const SIGNS_OF_USE = ["pointermove", "pointerdown", "keydown", "scroll"] as const;

/** The one holder for a site key, and whether anything on the page needs it. */
class SharedCheck {
  private users = 0;
  readonly holder: TokenHolder;

  constructor(siteKey: string) {
    this.holder = new TokenHolder({
      runAhead: (signal) => runCheck(siteKey, backstage(), { attended: false, signal }),
      mayRunAhead: () => this.users > 0 && document.visibilityState === "visible",
      whenIdle,
    });
  }

  private readonly prepare = () => this.holder.prepare();
  private readonly prepareWhenShown = () => {
    if (document.visibilityState === "visible") whenIdle(this.prepare);
  };

  /** Counts a user of the holder, until the function it returns is called. */
  join(): () => void {
    this.users += 1;
    if (this.users === 1) {
      for (const name of SIGNS_OF_USE) window.addEventListener(name, this.prepare, { passive: true, capture: true });
      document.addEventListener("visibilitychange", this.prepareWhenShown);
    }
    afterLoad(this.prepare);
    return () => {
      this.users -= 1;
      if (this.users > 0) return;
      for (const name of SIGNS_OF_USE) window.removeEventListener(name, this.prepare, { capture: true });
      document.removeEventListener("visibilitychange", this.prepareWhenShown);
    };
  }
}

const shared = new Map<string, SharedCheck>();

function sharedCheck(siteKey: string): SharedCheck {
  let check = shared.get(siteKey);
  if (!check) shared.set(siteKey, (check = new SharedCheck(siteKey)));
  return check;
}

/**
 * `token()` for one use, and the container where a challenge shows if the
 * person has to answer one. `needed: false` says this user is done, so no
 * check runs ahead of time on its account.
 */
export function useTurnstile(siteKey: string | null, { needed = true }: { needed?: boolean } = {}) {
  const container = useRef<HTMLDivElement>(null);
  const stop = useRef<AbortController | null>(null);

  useEffect(() => () => stop.current?.abort(), []);

  useEffect(() => (siteKey && needed ? sharedCheck(siteKey).join() : undefined), [siteKey, needed]);

  const token = useCallback(async (): Promise<string> => {
    if (!siteKey) throw new TurnstileError("no site key");
    return sharedCheck(siteKey).holder.token(() => {
      stop.current?.abort();
      stop.current = new AbortController();
      return runCheck(siteKey, container.current, { attended: true, signal: stop.current.signal });
    });
  }, [siteKey]);

  return { container, token };
}
