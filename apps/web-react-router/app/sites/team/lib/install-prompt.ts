/**
 * The browser's own "install this app" prompt (Chrome and Edge, on Android
 * and desktop). The browser offers it once per page load, in an event that
 * may fire before anyone has signed in, so it is kept here, outside React,
 * for whichever team page later shows an Install button. iOS has no such
 * prompt: there the pages explain Add to Home Screen.
 */

type InstallPromptEvent = Event & {
  prompt(): Promise<{ outcome: "accepted" | "dismissed" } | void>;
  userChoice?: Promise<{ outcome: "accepted" | "dismissed" }>;
};

export type InstallOffer = {
  /** The browser has offered its prompt and it has not been used yet. */
  available: boolean;
  /** The app was installed from this page, by this prompt or the browser's menu. */
  installed: boolean;
};

const NONE: InstallOffer = { available: false, installed: false };
let offer: InstallOffer = NONE;
let prompt: InstallPromptEvent | null = null;
let listening = false;
const listeners = new Set<() => void>();

function set(next: InstallOffer) {
  offer = next;
  for (const listener of listeners) listener();
}

/** Starts keeping the browser's install prompt. Safe to call on every render of the team layout. */
export function keepInstallPrompt(target: Pick<Window, "addEventListener"> = window): void {
  if (listening) return;
  listening = true;
  target.addEventListener("beforeinstallprompt", (event) => {
    // The page offers installing itself, in its own words.
    event.preventDefault();
    prompt = event as InstallPromptEvent;
    set({ ...offer, available: true });
  });
  target.addEventListener("appinstalled", () => {
    prompt = null;
    set({ available: false, installed: true });
  });
}

export function subscribeToInstallOffer(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function installOffer(): InstallOffer {
  return offer;
}

/** What the server renders: no offer. */
export function noInstallOffer(): InstallOffer {
  return NONE;
}

/**
 * Shows the browser's install prompt. A prompt can be shown once, so the
 * offer is gone afterwards whatever the answer; the browser offers again on a
 * later visit if the answer was no.
 */
export async function showInstallPrompt(): Promise<"accepted" | "dismissed" | "unavailable"> {
  const event = prompt;
  if (!event) return "unavailable";
  prompt = null;
  try {
    const answered = await event.prompt();
    const outcome = (answered ?? (await event.userChoice))?.outcome ?? "dismissed";
    set({ available: false, installed: offer.installed || outcome === "accepted" });
    return outcome;
  } catch {
    set({ ...offer, available: false });
    return "unavailable";
  }
}

/** For tests: forget everything, as a new page load does. */
export function resetInstallPrompt(): void {
  offer = NONE;
  prompt = null;
  listening = false;
  listeners.clear();
}
