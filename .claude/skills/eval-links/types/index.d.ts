export type EvalLink = {
  label: string
  href: string
  /** What the page is for, in a few words. */
  note: string
  /** The server behind it: 'up', 'down', or 'unknown' when it could not be checked. */
  status: 'up' | 'down' | 'unknown'
  /** The command that starts that server. */
  start: string
}

declare module 'claude-code' {
  interface PluginState {
    'eval-links': { links: Shaped<EvalLink[]> }
  }
}
