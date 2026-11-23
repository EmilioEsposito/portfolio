/**
 * Holds one Turnstile token got ahead of time, so the person who needs it does
 * not wait for the check. Kept apart from the DOM so it can be tested; the
 * widget is in ~/sites/sernia-public/components/turnstile.tsx.
 */

/** A token works for 300 seconds. One held longer than this counts as expired. */
export const TOKEN_MAX_AGE_MS = 4 * 60_000;

/** A check ahead of time that has not answered by then is dropped, so a person never waits long on a stuck one. */
export const AHEAD_LIMIT_MS = 10_000;

export class TurnstileError extends Error {}

export type TokenHolderOptions = {
  /** Runs a check nobody is waiting for. It rejects when it fails, would need the person, or `signal` aborts. */
  runAhead: (signal: AbortSignal) => Promise<string>;
  /** Whether a check may start ahead of time right now. */
  mayRunAhead: () => boolean;
  /** Runs its argument once the page is idle. */
  whenIdle: (run: () => void) => void;
};

/**
 * Several users (the call line and the booking form) share one holder. Each
 * token goes to one of them, because a token works once.
 */
export class TokenHolder {
  private held: { token: string; at: number } | null = null;
  private ahead: { check: Promise<string>; claimed: boolean } | null = null;
  /** The last check failed: nothing more runs ahead of time until a person's own check passes. */
  private failed = false;

  constructor(private readonly options: TokenHolderOptions) {}

  /** Gets a token ready, unless a fresh one is held, a check is under way or the last one failed. Never throws. */
  prepare(): void {
    if (this.failed || this.ahead || this.isFresh() || !this.options.mayRunAhead()) return;
    const limit = new AbortController();
    const timer = setTimeout(() => limit.abort(), AHEAD_LIMIT_MS);
    const ahead = { check: this.options.runAhead(limit.signal), claimed: false };
    this.ahead = ahead;
    ahead.check
      .then(
        (token) => {
          if (!ahead.claimed) this.held = { token, at: Date.now() };
        },
        () => {
          this.failed = true;
        },
      )
      .finally(() => {
        clearTimeout(timer);
        this.ahead = null;
      });
  }

  /**
   * A token for one use: the held one when it is fresh, else the one from a
   * check already under way, else from `runNow`, the person's own check. Then
   * gets the next one ready.
   */
  async token(runNow: () => Promise<string>): Promise<string> {
    try {
      const token = this.takeHeld() ?? (await this.claimAhead()) ?? (await runNow());
      this.failed = false;
      return token;
    } catch (error) {
      this.failed = true;
      throw error;
    } finally {
      this.options.whenIdle(() => this.prepare());
    }
  }

  private isFresh(): boolean {
    if (!this.held) return false;
    const age = Date.now() - this.held.at;
    return age >= 0 && age < TOKEN_MAX_AGE_MS;
  }

  private takeHeld(): string | null {
    const token = this.isFresh() ? (this.held?.token ?? null) : null;
    this.held = null;
    return token;
  }

  private async claimAhead(): Promise<string | null> {
    const ahead = this.ahead;
    if (!ahead || ahead.claimed) return null;
    ahead.claimed = true;
    return ahead.check.catch(() => null);
  }
}
