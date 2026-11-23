/**
 * What this device can do about notifications and installing the team's
 * pages, and which reminder (if any) a team page shows for it. No React and
 * no browser globals at import time: the facts are read through `readDevice`
 * and everything else is decided from plain values.
 */

/** What the browser says about itself, read once the page is open. */
export type DeviceFacts = {
  /** An iPhone or iPad, in any browser (they are all WebKit). */
  ios: boolean;
  /** iOS 16.4 or later, the first to push to a Home Screen web app. */
  iosCanPush: boolean;
  /** Which iOS browser, for where its Share button is. */
  iosBrowser: "safari" | "chrome" | null;
  /** Opened from the home screen or as an installed app, not in a browser tab. */
  standalone: boolean;
  /** The browser has service workers, push and notifications. */
  pushSupported: boolean;
  /** A phone or tablet, by what the browser is and not by window width. */
  mobile: boolean;
};

type NavigatorLike = { userAgent: string; platform?: string; maxTouchPoints?: number; standalone?: boolean };
type WindowLike = { matchMedia(query: string): { matches: boolean } };

export function isStandalone(navigator: NavigatorLike, window: WindowLike): boolean {
  return window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
}

/** `globals` is `window`: the push check looks for the names on it. */
export function readDevice(navigator: NavigatorLike, window: WindowLike, globals: object): DeviceFacts {
  const agent = navigator.userAgent;
  // iPadOS calls itself a Mac; a Mac with a touch screen is an iPad.
  const ios =
    /iPad|iPhone|iPod/.test(agent) ||
    (/Macintosh/.test(agent) && navigator.platform === "MacIntel" && (navigator.maxTouchPoints ?? 0) > 1);
  return {
    ios,
    iosCanPush: ios && iosVersionCanPush(agent),
    iosBrowser: ios ? (/CriOS/.test(agent) ? "chrome" : "safari") : null,
    standalone: isStandalone(navigator, window),
    pushSupported: "serviceWorker" in navigator && "PushManager" in globals && "Notification" in globals,
    mobile: ios || /Android|Mobile/i.test(agent),
  };
}

/** An agent this cannot read counts as able: a parsing miss should not hide the steps. */
function iosVersionCanPush(agent: string): boolean {
  // "iPhone OS 16_4 like Mac OS X"; an iPad posing as a Mac has only Safari's "Version/16.4".
  const version = agent.match(/OS (\d+)_(\d+)/) ?? agent.match(/Version\/(\d+)\.(\d+)/);
  if (!version) return true;
  const [major, minor] = [Number(version[1]), Number(version[2])];
  return major > 16 || (major === 16 && minor >= 4);
}

/**
 * Where notifications stand on this device.
 * - `needs-home-screen`: an iPhone or iPad in a browser tab; iOS only pushes
 *   to the app on the Home Screen.
 * - `in-app`: the same browser tab, for an account that already gets
 *   notifications in the Home Screen app of an iPhone or iPad.
 * - `blocked`: the browser has been told not to ask for this site.
 * - `unavailable`: this browser cannot do it at all.
 */
export type NotificationStatus = "checking" | "on" | "off" | "blocked" | "needs-home-screen" | "in-app" | "unavailable";

export function notificationStatus(
  device: DeviceFacts,
  push: {
    checked: boolean;
    permission: NotificationPermission | "unsupported";
    subscribed: boolean;
    /**
     * For an iPhone or iPad in a browser tab: the API says this account has
     * the Home Screen app subscribed. Null while asking, or when asking failed.
     */
    iosApp?: boolean | null;
  },
): NotificationStatus {
  // Asked first: an iPhone's browser tab has no push at all, so it would
  // otherwise read as a browser that can never notify.
  if (device.ios && !device.standalone) {
    if (!device.iosCanPush) return "unavailable";
    return push.iosApp === true ? "in-app" : "needs-home-screen";
  }
  if (!device.pushSupported) return "unavailable";
  if (push.permission === "denied") return "blocked";
  if (!push.checked) return "checking";
  // A subscription the browser no longer allows delivers nothing: resetting
  // the site's permission ends it, whatever this page last knew.
  return push.subscribed && push.permission === "granted" ? "on" : "off";
}

/** The word for the status, in the sidebar's Notifications row and the needs-attention button. */
export const STATUS_WORD: Record<NotificationStatus, string> = {
  checking: "Checking",
  on: "On",
  off: "Off",
  blocked: "Blocked",
  "needs-home-screen": "Off",
  "in-app": "On in the app",
  unavailable: "Not available",
};

/**
 * The status's colour: green when on (here or in the app), amber when off,
 * red when the browser blocks it, grey where there is nothing to do.
 */
export function statusTone(status: NotificationStatus): "on" | "off" | "blocked" | "neutral" {
  if (status === "on" || status === "in-app") return "on";
  if (status === "blocked") return "blocked";
  return status === "checking" || status === "unavailable" ? "neutral" : "off";
}

/** Off or blocked: a person can fix it, so the page's top bar says so. Nothing shows when all is well. */
export function needsAttention(status: NotificationStatus): boolean {
  const tone = statusTone(status);
  return tone === "off" || tone === "blocked";
}

/**
 * Clears the app icon's badge, which counts notifications not yet opened:
 * opening the app is opening them. The Badging API is only in some browsers
 * (installed apps on iOS 16.4+, desktop Chrome and Edge), so this does nothing
 * elsewhere and never throws.
 */
export function clearAppBadge(navigator: object): void {
  const badging = navigator as { clearAppBadge?: () => Promise<void> };
  if (typeof badging.clearAppBadge !== "function") return;
  try {
    void badging.clearAppBadge().catch(() => {});
  } catch {
    // Refused at once: there is no badge to clear.
  }
}

// ---------------------------------------------------------------------------
// Reminders
// ---------------------------------------------------------------------------

/** How often a reminder was put away, and until when it stays away. */
export type Reminder = { dismissals: number; remindAfter: number };
export type Reminders = { notifications: Reminder; install: Reminder };

export const REMINDERS_STORAGE_KEY = "sernia-team-reminders";
export const NO_REMINDERS: Reminders = {
  notifications: { dismissals: 0, remindAfter: 0 },
  install: { dismissals: 0, remindAfter: 0 },
};
const WEEK_MS = 7 * 24 * 60 * 60 * 1000;
/** Put away this many times, a reminder does not come back. */
const LAST_DISMISSAL = 2;

function readReminder(value: unknown): Reminder {
  const reminder = value as Partial<Reminder> | null;
  if (
    reminder &&
    Number.isInteger(reminder.dismissals) &&
    Number(reminder.dismissals) >= 0 &&
    Number.isFinite(reminder.remindAfter) &&
    Number(reminder.remindAfter) >= 0
  ) {
    return { dismissals: Math.min(LAST_DISMISSAL, Number(reminder.dismissals)), remindAfter: Number(reminder.remindAfter) };
  }
  return NO_REMINDERS.notifications;
}

/** What the browser stored, or no reminders put away when it stored nothing usable. */
export function parseReminders(stored: string | null): Reminders {
  try {
    const value = JSON.parse(stored ?? "null") as Partial<Record<keyof Reminders, unknown>> | null;
    return { notifications: readReminder(value?.notifications), install: readReminder(value?.install) };
  } catch {
    return NO_REMINDERS;
  }
}

export function shows(reminder: Reminder, now: number): boolean {
  return reminder.dismissals < LAST_DISMISSAL && now >= reminder.remindAfter;
}

/** Put away for a week; the second time, for good. */
export function putAway(reminder: Reminder, now: number): Reminder {
  return { dismissals: Math.min(LAST_DISMISSAL, reminder.dismissals + 1), remindAfter: now + WEEK_MS };
}

/** Put away for good: the person chose this state themselves. */
export function putAwayForGood(now: number): Reminder {
  return { dismissals: LAST_DISMISSAL, remindAfter: now };
}

/** Whether putting this reminder away now is the last time it is offered. */
export function isLastOffer(reminder: Reminder): boolean {
  return reminder.dismissals >= LAST_DISMISSAL - 1;
}

/**
 * The one reminder a team page shows, most useful first: turning notifications
 * on, then installing on a phone. `null` when there is nothing to ask, or the
 * person has put it away.
 */
export type BannerKind = "turn-on" | "home-screen" | "blocked" | "install";

export function bannerFor(input: {
  status: NotificationStatus;
  /** The browser has offered its own install prompt. */
  installable: boolean;
  device: Pick<DeviceFacts, "mobile" | "standalone">;
  reminders: Reminders;
  now: number;
}): BannerKind | null {
  const { status, reminders, now } = input;
  // "in-app" asks nothing: the account already gets notifications in the app.
  if (status === "off" || status === "needs-home-screen" || status === "blocked") {
    if (!shows(reminders.notifications, now)) return null;
    return status === "off" ? "turn-on" : status === "blocked" ? "blocked" : "home-screen";
  }
  // Installing is only pressed on a phone, where it puts the pages one tap
  // away; a desktop finds it in the notifications panel.
  if (status === "on" && input.installable && input.device.mobile && !input.device.standalone && shows(reminders.install, now)) {
    return "install";
  }
  return null;
}
