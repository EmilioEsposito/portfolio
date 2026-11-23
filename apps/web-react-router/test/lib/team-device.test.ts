import assert from "node:assert/strict";
import test from "node:test";
import {
  installOffer,
  keepInstallPrompt,
  resetInstallPrompt,
  showInstallPrompt,
  subscribeToInstallOffer,
} from "../../app/sites/team/lib/install-prompt.ts";
import {
  bannerFor,
  isLastOffer,
  NO_REMINDERS,
  notificationStatus,
  parseReminders,
  putAway,
  putAwayForGood,
  readDevice,
  shows,
  statusTone,
  STATUS_WORD,
  type DeviceFacts,
  type NotificationStatus,
} from "../../app/sites/team/lib/team-device.ts";

const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1";
const OLD_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Mobile/15E148 Safari/604.1";
const IPHONE_CHROME = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/140.0.0.0 Mobile/15E148 Safari/604.1";
const IPAD_AS_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15";
const ANDROID = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36";
const DESKTOP = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";

const PUSH = { PushManager: {}, Notification: {} };
const tab = { matchMedia: () => ({ matches: false }) };
const installedApp = { matchMedia: () => ({ matches: true }) };

function browser(userAgent: string, extra: Record<string, unknown> = {}) {
  return { userAgent, platform: "", maxTouchPoints: 0, serviceWorker: {}, ...extra };
}

test("an iPhone's browser tab has no push; its Home Screen app has", () => {
  // Safari on iOS has no PushManager outside the Home Screen app.
  const inTab = readDevice(browser(IPHONE), tab, {});
  assert.deepEqual(inTab, { ios: true, iosCanPush: true, iosBrowser: "safari", standalone: false, pushSupported: false, mobile: true });
  assert.equal(readDevice(browser(IPHONE_CHROME), tab, {}).iosBrowser, "chrome");
  // iOS before 16.4 cannot push at all, so nobody is walked through installing for nothing.
  assert.equal(readDevice(browser(OLD_IPHONE), tab, {}).iosCanPush, false);
  // An iPad says it is a Mac; its touch screen gives it away.
  const ipad = readDevice(browser(IPAD_AS_MAC, { platform: "MacIntel", maxTouchPoints: 5 }), tab, {});
  assert.equal(ipad.ios, true);
  assert.equal(ipad.iosCanPush, true);
  assert.equal(readDevice(browser(IPAD_AS_MAC, { platform: "MacIntel" }), tab, PUSH).ios, false);
  // iOS marks the Home Screen app on navigator, not in the display mode.
  const fromHomeScreen = readDevice(browser(IPHONE, { standalone: true }), tab, PUSH);
  assert.equal(fromHomeScreen.standalone, true);
  assert.equal(fromHomeScreen.pushSupported, true);
});

test("Android and desktop browsers are read as what they are", () => {
  assert.deepEqual(readDevice(browser(ANDROID), tab, PUSH), {
    ios: false, iosCanPush: false, iosBrowser: null, standalone: false, pushSupported: true, mobile: true,
  });
  assert.equal(readDevice(browser(ANDROID), installedApp, PUSH).standalone, true);
  // A desktop browser's phone preview keeps the Mac's platform and adds touch: still Android.
  assert.equal(readDevice(browser(ANDROID, { platform: "MacIntel", maxTouchPoints: 5 }), tab, PUSH).ios, false);
  const desktop = readDevice(browser(DESKTOP), tab, PUSH);
  assert.equal(desktop.mobile, false);
  assert.equal(desktop.pushSupported, true);
  // A browser without service workers cannot push, whatever else it has.
  assert.equal(readDevice({ userAgent: DESKTOP }, tab, PUSH).pushSupported, false);
});

const android: DeviceFacts = readDevice(browser(ANDROID), tab, PUSH);
const iphoneTab: DeviceFacts = readDevice(browser(IPHONE), tab, {});
const iphoneApp: DeviceFacts = readDevice(browser(IPHONE, { standalone: true }), tab, PUSH);

test("where notifications stand on a device", () => {
  const push = (permission: NotificationPermission, subscribed: boolean, checked = true) => ({ permission, subscribed, checked });
  assert.equal(notificationStatus(android, push("default", false)), "off");
  // Allowed in the browser but not subscribed for this user is still off.
  assert.equal(notificationStatus(android, push("granted", false)), "off");
  assert.equal(notificationStatus(android, push("granted", true)), "on");
  // A subscription whose permission was reset in the browser's settings is not on.
  assert.equal(notificationStatus(android, push("default", true)), "off");
  assert.equal(notificationStatus(android, push("denied", false)), "blocked");
  // Blocked is known at once; on or off only after the subscription is checked.
  assert.equal(notificationStatus(android, push("denied", false, false)), "blocked");
  assert.equal(notificationStatus(android, push("granted", false, false)), "checking");
  // An iPhone in a browser tab is asked to use the Home Screen, never told it cannot.
  assert.equal(notificationStatus(iphoneTab, push("default", false)), "needs-home-screen");
  assert.equal(notificationStatus(readDevice(browser(OLD_IPHONE), tab, {}), push("default", false)), "unavailable");
  assert.equal(notificationStatus(iphoneApp, push("default", false)), "off");
  assert.equal(notificationStatus(iphoneApp, push("granted", true)), "on");
  assert.equal(notificationStatus(readDevice(browser(DESKTOP), tab, {}), push("default", false)), "unavailable");
});

test("the dot is green only when on, red for what a person can fix, grey otherwise", () => {
  const tones = Object.fromEntries(
    (Object.keys(STATUS_WORD) as NotificationStatus[]).map((status) => [status, statusTone(status)]),
  );
  assert.deepEqual(tones, {
    checking: "neutral", on: "on", off: "off", blocked: "off", "needs-home-screen": "off", unavailable: "neutral",
  });
  assert.equal(STATUS_WORD.on, "On");
  assert.equal(STATUS_WORD.off, "Off");
  assert.equal(STATUS_WORD["needs-home-screen"], "Off");
  assert.equal(STATUS_WORD.blocked, "Blocked");
});

const NOW = 1_800_000_000_000;
const WEEK = 7 * 24 * 60 * 60 * 1000;

test("a reminder put away stays away a week, and the second time for good", () => {
  const fresh = NO_REMINDERS.notifications;
  assert.equal(shows(fresh, NOW), true);
  assert.equal(isLastOffer(fresh), false);
  const once = putAway(fresh, NOW);
  assert.equal(shows(once, NOW + WEEK - 1), false);
  assert.equal(shows(once, NOW + WEEK), true);
  assert.equal(isLastOffer(once), true);
  const twice = putAway(once, NOW + WEEK);
  assert.equal(shows(twice, NOW + 100 * WEEK), false);
  // Turning notifications off oneself puts the reminder away at once.
  assert.equal(shows(putAwayForGood(NOW), NOW + 100 * WEEK), false);
});

test("stored reminders are read back, and anything else counts as none", () => {
  const stored = JSON.stringify({ notifications: putAway(NO_REMINDERS.notifications, NOW), install: { dismissals: 9, remindAfter: 5 } });
  assert.deepEqual(parseReminders(stored), {
    notifications: { dismissals: 1, remindAfter: NOW + WEEK },
    install: { dismissals: 2, remindAfter: 5 },
  });
  for (const junk of [null, "", "not json", "[]", '{"notifications":{"dismissals":-1,"remindAfter":0}}', '{"install":"x"}']) {
    assert.deepEqual(parseReminders(junk), NO_REMINDERS, String(junk));
  }
});

test("the one banner a page shows", () => {
  const ask = (status: NotificationStatus, more: Partial<Parameters<typeof bannerFor>[0]> = {}) =>
    bannerFor({ status, installable: false, device: android, reminders: NO_REMINDERS, now: NOW, ...more });
  assert.equal(ask("off"), "turn-on");
  assert.equal(ask("needs-home-screen", { device: iphoneTab }), "home-screen");
  assert.equal(ask("blocked"), "blocked");
  // Nothing to ask while it is being checked, where it cannot work, or once it is on.
  assert.equal(ask("checking"), null);
  assert.equal(ask("unavailable"), null);
  assert.equal(ask("on"), null);
  // Turning notifications on comes before installing.
  assert.equal(ask("off", { installable: true }), "turn-on");
  assert.equal(ask("on", { installable: true }), "install");
  // Installing is only pressed on a phone, and never inside the installed app.
  assert.equal(ask("on", { installable: true, device: readDevice(browser(DESKTOP), tab, PUSH) }), null);
  assert.equal(ask("on", { installable: true, device: readDevice(browser(ANDROID), installedApp, PUSH) }), null);
  // Each reminder is put away on its own.
  const quiet = { ...NO_REMINDERS, notifications: putAway(NO_REMINDERS.notifications, NOW) };
  assert.equal(ask("off", { reminders: quiet }), null);
  assert.equal(ask("blocked", { reminders: quiet }), null);
  assert.equal(ask("on", { installable: true, reminders: quiet }), "install");
  assert.equal(ask("on", { installable: true, reminders: { ...NO_REMINDERS, install: putAway(NO_REMINDERS.install, NOW) } }), null);
  assert.equal(ask("off", { reminders: quiet, now: NOW + WEEK }), "turn-on");
});

function fakeWindow() {
  const handlers = new Map<string, (event: Event) => void>();
  return {
    addEventListener: ((type: string, handler: (event: Event) => void) => handlers.set(type, handler)) as Window["addEventListener"],
    fire: (type: string, event: object = {}) => handlers.get(type)?.(event as Event),
    count: () => handlers.size,
  };
}

test("the browser's install prompt is kept until a page uses it, once", async () => {
  resetInstallPrompt();
  const page = fakeWindow();
  keepInstallPrompt(page);
  keepInstallPrompt(page);
  assert.equal(page.count(), 2);
  assert.deepEqual(installOffer(), { available: false, installed: false });
  assert.equal(await showInstallPrompt(), "unavailable");

  let changes = 0;
  const stop = subscribeToInstallOffer(() => changes++);
  let prevented = false;
  let shown = 0;
  page.fire("beforeinstallprompt", {
    preventDefault: () => (prevented = true),
    prompt: async () => {
      shown += 1;
      return { outcome: "dismissed" };
    },
  });
  // The browser's own bar is held back: the page offers installing in its own words.
  assert.equal(prevented, true);
  assert.deepEqual(installOffer(), { available: true, installed: false });
  assert.equal(await showInstallPrompt(), "dismissed");
  assert.deepEqual(installOffer(), { available: false, installed: false });
  // A prompt cannot be shown twice.
  assert.equal(await showInstallPrompt(), "unavailable");
  assert.equal(shown, 1);
  assert.ok(changes >= 2);
  stop();
});

test("an accepted prompt, an older browser's answer and an install from the menu all count as installed", async () => {
  resetInstallPrompt();
  const page = fakeWindow();
  keepInstallPrompt(page);
  page.fire("beforeinstallprompt", { preventDefault() {}, prompt: async () => ({ outcome: "accepted" }) });
  assert.equal(await showInstallPrompt(), "accepted");
  assert.deepEqual(installOffer(), { available: false, installed: true });

  resetInstallPrompt();
  keepInstallPrompt(page);
  // Older Chrome resolves prompt() with nothing and answers in userChoice.
  page.fire("beforeinstallprompt", { preventDefault() {}, prompt: async () => undefined, userChoice: Promise.resolve({ outcome: "accepted" }) });
  assert.equal(await showInstallPrompt(), "accepted");

  resetInstallPrompt();
  keepInstallPrompt(page);
  page.fire("beforeinstallprompt", { preventDefault() {}, prompt: async () => ({ outcome: "dismissed" }) });
  page.fire("appinstalled");
  assert.deepEqual(installOffer(), { available: false, installed: true });
  assert.equal(await showInstallPrompt(), "unavailable");

  resetInstallPrompt();
  keepInstallPrompt(page);
  page.fire("beforeinstallprompt", {
    preventDefault() {},
    prompt: async () => {
      throw new Error("no gesture");
    },
  });
  assert.equal(await showInstallPrompt(), "unavailable");
  assert.deepEqual(installOffer(), { available: false, installed: false });
});
