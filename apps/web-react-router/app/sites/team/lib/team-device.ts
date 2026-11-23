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
 * - `blocked`: the browser has been told not to ask for this site.
 * - `unavailable`: this browser cannot do it at all.
 */
export type NotificationStatus = "checking" | "on" | "off" | "blocked" | "needs-home-screen" | "unavailable";

export function notificationStatus(
  device: DeviceFacts,
  push: { checked: boolean; permission: NotificationPermission | "unsupported"; subscribed: boolean },
): NotificationStatus {
  // Asked first: an iPhone's browser tab has no push at all, so it would
  // otherwise read as a browser that can never notify.
  if (device.ios && !device.standalone) return device.iosCanPush ? "needs-home-screen" : "unavailable";
  if (!device.pushSupported) return "unavailable";
  if (push.permission === "denied") return "blocked";
  if (!push.checked) return "checking";
  return push.subscribed ? "on" : "off";
}

/** The word shown next to the bell. */
export const STATUS_WORD: Record<NotificationStatus, string> = {
  checking: "Checking",
  on: "On",
  off: "Off",
  blocked: "Blocked",
  "needs-home-screen": "Off",
  unavailable: "Not available",
};

/** Green for on, red for anything a person can fix, grey where there is nothing to do. */
export function statusTone(status: NotificationStatus): "on" | "off" | "neutral" {
  if (status === "on") return "on";
  return status === "checking" || status === "unavailable" ? "neutral" : "off";
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
