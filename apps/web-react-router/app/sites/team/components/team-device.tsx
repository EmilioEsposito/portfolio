import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { Bell, BellOff, Download, Loader2, Share, X } from "lucide-react";

import { Button } from "~/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "~/components/ui/dialog";
import { SidebarMenuButton, SidebarMenuItem } from "~/components/ui/sidebar";
import { cn } from "~/lib/utils";
import { usePushNotifications, type PushFailure } from "~/sites/team/hooks/use-push-notifications";
import {
  installOffer,
  noInstallOffer,
  showInstallPrompt,
  subscribeToInstallOffer,
  type InstallOffer,
} from "~/sites/team/lib/install-prompt";
import {
  bannerFor,
  isLastOffer,
  NO_REMINDERS,
  notificationStatus,
  parseReminders,
  putAway,
  putAwayForGood,
  REMINDERS_STORAGE_KEY,
  STATUS_WORD,
  statusTone,
  type BannerKind,
  type DeviceFacts,
  type NotificationStatus,
  type Reminders,
} from "~/sites/team/lib/team-device";

// Notifications and installing, the same on every team page:
// - the bell says where notifications stand on this device (a dot and a
//   word; it never switches anything by itself) and opens the panel;
// - the panel says it in a sentence and holds the buttons, each named for
//   what it does;
// - the banner asks, until the person acts or puts it away.

type TestSend = "idle" | "sending" | "sent" | "failed";

/** How long "Notifications are on for this device" stays after turning them on. */
const TURNED_ON_SHOWS_MS = 30_000;

type TeamDevice = {
  status: NotificationStatus;
  device: DeviceFacts | null;
  busy: boolean;
  failure: PushFailure | null;
  /** Just turned on from this page: the banner says so once. */
  turnedOn: boolean;
  test: TestSend;
  banner: BannerKind | null;
  /** Putting the banner away now is the last time it is offered. */
  lastOffer: boolean;
  openPanel: () => void;
  turnOn: () => void;
  turnOff: () => void;
  sendTest: () => void;
  installApp: () => void;
  dismissBanner: () => void;
  dismissTurnedOn: () => void;
};

const TeamDeviceContext = createContext<TeamDevice | null>(null);

function useTeamDevice(): TeamDevice {
  const value = useContext(TeamDeviceContext);
  if (!value) throw new Error("useTeamDevice needs TeamDeviceProvider");
  return value;
}

function saveReminders(reminders: Reminders) {
  try {
    localStorage.setItem(REMINDERS_STORAGE_KEY, JSON.stringify(reminders));
  } catch {
    // Storage is blocked: the reminder stays away until the page is reloaded.
  }
}

/** Holds the one notification subscription and the reminders for every team page under it. */
export function TeamDeviceProvider({ children }: { children: React.ReactNode }) {
  const push = usePushNotifications();
  const offer = useSyncExternalStore(subscribeToInstallOffer, installOffer, noInstallOffer);
  const [open, setOpen] = useState(false);
  // Null until the browser's stored reminders are read, so that a banner the
  // person put away does not show for a moment on every page.
  const [reminders, setReminders] = useState<Reminders | null>(null);
  const [now, setNow] = useState(0);
  const [turnedOn, setTurnedOn] = useState(false);
  const [test, setTest] = useState<TestSend>("idle");

  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(REMINDERS_STORAGE_KEY);
    } catch {
      // Storage is blocked: nothing was put away.
    }
    setReminders(parseReminders(stored));
    setNow(Date.now());
  }, []);

  const status: NotificationStatus = push.device
    ? notificationStatus(push.device, { checked: push.checked, permission: push.permission, subscribed: push.isSubscribed })
    : "checking";

  const remember = useCallback((topic: keyof Reminders, change: (reminder: Reminders[keyof Reminders], now: number) => Reminders[keyof Reminders]) => {
    const at = Date.now();
    setReminders((current) => {
      const next = { ...(current ?? NO_REMINDERS) };
      next[topic] = change(next[topic], at);
      saveReminders(next);
      return next;
    });
    setNow(at);
  }, []);

  const { subscribe, unsubscribe, sendTest: sendTestPush, recheckPermission } = push;
  const turnOn = useCallback(() => {
    void subscribe().then((subscribed) => {
      if (!subscribed) return;
      setTurnedOn(true);
      setTest("idle");
      // Should this device lose its subscription later (signing out ends it),
      // the banner asks again.
      remember("notifications", () => NO_REMINDERS.notifications);
    });
  }, [remember, subscribe]);
  const turnOff = useCallback(() => {
    setTurnedOn(false);
    setTest("idle");
    void unsubscribe().then((off) => {
      // Off is what the person chose: the banner does not ask them to undo it.
      if (off) remember("notifications", (_, at) => putAwayForGood(at));
    });
  }, [remember, unsubscribe]);
  const sendTest = useCallback(() => {
    setTest("sending");
    void sendTestPush().then((sent) => setTest(sent ? "sent" : "failed"));
  }, [sendTestPush]);

  // "Notifications are on" is said once and then leaves by itself, later if a
  // test is on its way.
  useEffect(() => {
    if (!turnedOn || test === "sending") return;
    const timer = setTimeout(() => setTurnedOn(false), TURNED_ON_SHOWS_MS);
    return () => clearTimeout(timer);
  }, [turnedOn, test]);

  const banner =
    reminders && push.device
      ? bannerFor({ status, installable: offer.available, device: push.device, reminders, now })
      : null;
  const topic = banner === "install" ? "install" : "notifications";
  const dismissBanner = useCallback(() => remember(topic, putAway), [remember, topic]);
  const installApp = useCallback(() => {
    void showInstallPrompt().then((outcome) => {
      // A "no" to the browser's prompt is a "not now" to the banner as well.
      if (outcome !== "accepted") remember("install", putAway);
    });
  }, [remember]);

  const value = useMemo<TeamDevice>(
    () => ({
      status,
      device: push.device,
      busy: push.busy,
      failure: push.failure,
      turnedOn: turnedOn && status === "on",
      test,
      banner,
      lastOffer: reminders ? isLastOffer(reminders[topic]) : false,
      openPanel: () => setOpen(true),
      turnOn,
      turnOff,
      sendTest,
      installApp,
      dismissBanner,
      dismissTurnedOn: () => setTurnedOn(false),
    }),
    [banner, dismissBanner, installApp, push.busy, push.device, push.failure, reminders, sendTest, status, test, topic, turnOff, turnOn, turnedOn],
  );

  return (
    <TeamDeviceContext.Provider value={value}>
      {children}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-md">
          <NotificationsPanel
            status={status}
            device={push.device}
            install={offer}
            busy={push.busy}
            failure={push.failure}
            test={test}
            onTurnOn={turnOn}
            onTurnOff={turnOff}
            onSendTest={sendTest}
            onInstall={installApp}
            onRecheck={recheckPermission}
          />
        </DialogContent>
      </Dialog>
    </TeamDeviceContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// The bell: where notifications stand, never a switch
// ---------------------------------------------------------------------------

const DOT: Record<ReturnType<typeof statusTone>, string> = {
  on: "bg-emerald-500",
  off: "bg-red-500",
  neutral: "bg-muted-foreground/50",
};

/** A bell with a dot at its corner: green when on, red when not, grey when there is nothing to do. */
export function StatusBell({ status, className }: { status: NotificationStatus; className?: string }) {
  const tone = statusTone(status);
  const Icon = tone === "off" ? BellOff : Bell;
  return (
    <span className={cn("relative inline-flex size-4 shrink-0", className)} data-status={status}>
      <Icon className={cn("size-full", tone !== "on" && "text-muted-foreground")} aria-hidden="true" />
      <span
        className={cn("absolute -right-1 -top-1 size-2.5 rounded-full ring-2 ring-background", DOT[tone])}
        aria-hidden="true"
      />
    </span>
  );
}

/** What a screen reader and a tooltip say for the bell. */
export function bellLabel(status: NotificationStatus): string {
  return `Notifications on this device: ${STATUS_WORD[status].toLowerCase()}. Open notification settings`;
}

/**
 * The bell for a page's top bar. With `word`, the status is spelled out next
 * to it; without, the dot and the bell's shape carry it.
 */
export function NotificationsButton({ word = false, className }: { word?: boolean; className?: string }) {
  const { status, openPanel } = useTeamDevice();
  const spelled = word && status !== "checking" && status !== "unavailable";
  return (
    <Button
      variant="ghost"
      size={spelled ? "sm" : "icon"}
      className={cn("shrink-0", spelled ? "h-9 gap-2 px-2.5" : "h-8 w-8", className)}
      onClick={openPanel}
      title={bellLabel(status)}
      aria-label={bellLabel(status)}
    >
      <StatusBell status={status} />
      {spelled && <span className="text-xs font-medium">{STATUS_WORD[status]}</span>}
    </Button>
  );
}

/** The same bell as a row of the sidebar, with the status spelled out. */
export function NotificationsSidebarItem({ onOpen }: { onOpen?: () => void }) {
  const { status, openPanel } = useTeamDevice();
  return (
    <SidebarMenuItem>
      <SidebarMenuButton
        tooltip={`Notifications: ${STATUS_WORD[status]}`}
        aria-label={bellLabel(status)}
        onClick={() => {
          onOpen?.();
          openPanel();
        }}
      >
        <StatusBell status={status} />
        <span>Notifications</span>
        <span className="ml-auto text-xs text-muted-foreground" aria-hidden="true">
          {STATUS_WORD[status]}
        </span>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

// ---------------------------------------------------------------------------
// The banner
// ---------------------------------------------------------------------------

const BANNER: Record<BannerKind, { text: string; action: string; busyAction?: string }> = {
  "turn-on": {
    text: "Notifications are off on this device. Turn them on to be told when a conversation needs you.",
    action: "Turn on notifications",
    busyAction: "Turning on…",
  },
  "home-screen": {
    text: "Notifications are off on this device. An iPhone or iPad shows them once these pages are on its Home Screen.",
    action: "Show me how",
  },
  blocked: {
    text: "Notifications are blocked for this site in this browser, so you are not told when a conversation needs you.",
    action: "How to allow them",
  },
  install: {
    text: "Install the Sernia app to open the team pages from your home screen.",
    action: "Install app",
  },
};

/** The banner as it looks, from plain values. */
export function DeviceBannerView(props: {
  kind: BannerKind;
  lastOffer: boolean;
  busy: boolean;
  failure: PushFailure | null;
  onAct: () => void;
  onDismiss: () => void;
}) {
  const copy = BANNER[props.kind];
  const working = props.busy && props.kind === "turn-on";
  return (
    <section
      aria-label={props.kind === "install" ? "Install the app" : "Notifications on this device"}
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-muted/50 px-4 py-2.5 text-sm sm:px-6"
    >
      <p className="flex min-w-0 flex-1 basis-64 items-start gap-2.5">
        {props.kind === "install" ? (
          <Download className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        ) : (
          <StatusBell status={props.kind === "blocked" ? "blocked" : "off"} className="mt-0.5" />
        )}
        <span>
          {copy.text}
          {props.failure === "turn-on" && props.kind === "turn-on" && (
            <span role="alert" className="block font-medium text-red-600 dark:text-red-400">
              That did not work. Check the connection and try again.
            </span>
          )}
        </span>
      </p>
      <div className="flex shrink-0 items-center gap-1">
        <Button size="sm" className="h-8 gap-1.5" disabled={working} onClick={props.onAct}>
          {working && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
          {working ? copy.busyAction : copy.action}
        </Button>
        <Button variant="ghost" size="sm" className="h-8 text-muted-foreground" onClick={props.onDismiss}>
          {props.lastOffer ? "Don't remind me" : "Not now"}
        </Button>
      </div>
    </section>
  );
}

/** What the banner turns into once notifications were turned on from this page. */
export function TurnedOnBannerView(props: { test: TestSend; onSendTest: () => void; onDismiss: () => void }) {
  return (
    <section
      aria-label="Notifications on this device"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-muted/50 px-4 py-2.5 text-sm sm:px-6"
    >
      <p role="status" className="flex min-w-0 flex-1 basis-64 items-start gap-2.5">
        <StatusBell status="on" className="mt-0.5" />
        <span>
          Notifications are on for this device.
          <TestResult test={props.test} />
        </span>
      </p>
      <div className="flex shrink-0 items-center gap-1">
        <Button variant="outline" size="sm" className="h-8 gap-1.5" disabled={props.test === "sending"} onClick={props.onSendTest}>
          {props.test === "sending" && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
          {props.test === "sending" ? "Sending…" : "Send a test"}
        </Button>
        <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground" onClick={props.onDismiss}>
          <X className="size-4" aria-hidden="true" />
          <span className="sr-only">Close</span>
        </Button>
      </div>
    </section>
  );
}

function TestResult({ test }: { test: TestSend }) {
  if (test === "sent") return <span className="block text-muted-foreground">Test sent. It should arrive within a few seconds.</span>;
  if (test === "failed") {
    return (
      <span role="alert" className="block font-medium text-red-600 dark:text-red-400">
        The test could not be sent. Try again.
      </span>
    );
  }
  return null;
}

/** The banner of the page it is placed on; renders nothing when there is nothing to ask. */
export function TeamDeviceBanner() {
  const device = useTeamDevice();
  if (device.turnedOn) {
    return <TurnedOnBannerView test={device.test} onSendTest={device.sendTest} onDismiss={device.dismissTurnedOn} />;
  }
  if (!device.banner) return null;
  const act = device.banner === "turn-on" ? device.turnOn : device.banner === "install" ? device.installApp : device.openPanel;
  return (
    <DeviceBannerView
      kind={device.banner}
      lastOffer={device.lastOffer}
      busy={device.busy}
      failure={device.failure}
      onAct={act}
      onDismiss={device.dismissBanner}
    />
  );
}

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------

const PANEL: Record<NotificationStatus, { headline: string; detail: string }> = {
  checking: { headline: "Checking this device…", detail: "" },
  on: {
    headline: "On",
    detail: "This device is told when a conversation needs a person: an approval to give, an alert, or a reply that has finished.",
  },
  off: {
    headline: "Off",
    detail: "This device is not told when a conversation needs you. Your browser will ask you to allow notifications.",
  },
  blocked: {
    headline: "Blocked by this browser",
    detail: "This browser was told not to show notifications from this site, so the page cannot ask again.",
  },
  "needs-home-screen": {
    headline: "Off",
    detail: "An iPhone or iPad shows notifications only from an app on its Home Screen. Add these pages to it:",
  },
  unavailable: { headline: "Not available here", detail: "" },
};

function unavailableDetail(device: DeviceFacts | null): string {
  if (device?.ios && !device.iosCanPush) return "This iPhone or iPad needs iOS 16.4 or later to show notifications from these pages.";
  return "This browser cannot show notifications from web pages. Chrome, Edge, Firefox and Safari can.";
}

/** The panel's contents, from plain values. */
export function NotificationsPanel(props: {
  status: NotificationStatus;
  device: DeviceFacts | null;
  install: InstallOffer;
  busy: boolean;
  failure: PushFailure | null;
  test: TestSend;
  onTurnOn: () => void;
  onTurnOff: () => void;
  onSendTest: () => void;
  onInstall: () => void;
  onRecheck: () => void;
}) {
  const { status, device } = props;
  const copy = PANEL[status];
  const tone = statusTone(status);
  const standalone = device?.standalone ?? false;
  const showApp = standalone || props.install.installed || props.install.available;
  return (
    <>
      <DialogHeader>
        <DialogTitle>Notifications on this device</DialogTitle>
        <DialogDescription className="sr-only">Whether this device is told when a conversation needs a person.</DialogDescription>
      </DialogHeader>
      <div className="space-y-4">
        <div className="space-y-1.5" data-panel-status={status}>
          <p className="flex items-center gap-2.5 text-lg font-semibold">
            <span className={cn("size-3 shrink-0 rounded-full", DOT[tone])} aria-hidden="true" />
            {copy.headline}
          </p>
          <p className="text-sm text-muted-foreground">{status === "unavailable" ? unavailableDetail(device) : copy.detail}</p>
        </div>

        {status === "off" && (
          <Button className="w-full gap-2 sm:w-auto" disabled={props.busy} onClick={props.onTurnOn}>
            {props.busy && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
            {props.busy ? "Turning on…" : "Turn on notifications"}
          </Button>
        )}

        {status === "on" && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" className="gap-2" disabled={props.test === "sending" || props.busy} onClick={props.onSendTest}>
                {props.test === "sending" && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
                {props.test === "sending" ? "Sending…" : "Send a test"}
              </Button>
              <Button variant="ghost" className="gap-2 text-muted-foreground" disabled={props.busy} onClick={props.onTurnOff}>
                {props.busy && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
                {props.busy ? "Turning off…" : "Turn off"}
              </Button>
            </div>
            <p className="text-sm">
              <TestResult test={props.test} />
            </p>
          </div>
        )}

        {status === "blocked" && (
          <div className="space-y-3">
            <ol className="list-decimal space-y-1.5 pl-5 text-sm">
              <li>Open this site's settings: the icon at the left of the address bar. In an installed app, open the phone's settings for the app.</li>
              <li>
                Set <strong>Notifications</strong> to <strong>Allow</strong>.
              </li>
              <li>Come back here and press Check again.</li>
            </ol>
            <Button variant="outline" onClick={props.onRecheck}>
              Check again
            </Button>
          </div>
        )}

        {status === "needs-home-screen" && (
          <ol className="list-decimal space-y-1.5 pl-5 text-sm">
            <li>
              Tap <strong>Share</strong> <Share className="inline size-4 align-text-bottom" aria-hidden="true" />
              {device?.iosBrowser === "chrome"
                ? ", at the top right of the address bar."
                : ", at the bottom of the screen. If it is not there, tap ••• first."}
            </li>
            <li>
              Tap <strong>Add to Home Screen</strong>, then <strong>Add</strong>.
            </li>
            <li>Open Sernia from the Home Screen, sign in, and turn on notifications there.</li>
          </ol>
        )}

        {props.failure && (
          <p role="alert" className="text-sm font-medium text-red-600 dark:text-red-400">
            {props.failure === "turn-on"
              ? "Notifications could not be turned on. Check the connection and try again."
              : "Notifications could not be turned off. Check the connection and try again."}
          </p>
        )}

        {showApp && (
          <div className="space-y-2 border-t pt-4">
            <p className="text-sm font-semibold">App</p>
            {standalone ? (
              <p className="text-sm text-muted-foreground">You are using the installed app.</p>
            ) : props.install.installed ? (
              <p className="text-sm text-muted-foreground">Installed. Look for Sernia among your apps.</p>
            ) : (
              <>
                <p className="text-sm text-muted-foreground">
                  Install the app to open the team pages from your home screen or dock.
                </p>
                <Button variant="outline" className="gap-2" onClick={props.onInstall}>
                  <Download className="size-4" aria-hidden="true" />
                  Install app
                </Button>
              </>
            )}
          </div>
        )}
      </div>
    </>
  );
}
