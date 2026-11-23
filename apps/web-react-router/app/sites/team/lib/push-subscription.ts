function decodePublicKey(publicKey: string): Uint8Array<ArrayBuffer> {
  if (!publicKey) throw new Error("Notification key is not configured");
  const base64 = publicKey.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
}

/** Reuse only subscriptions signed by the server's current VAPID identity. */
export async function ensureCurrentPushSubscription(
  manager: Pick<PushManager, "getSubscription" | "subscribe">,
  publicKey: string,
): Promise<PushSubscription> {
  const expected = decodePublicKey(publicKey);
  const existing = await manager.getSubscription();
  if (existing) {
    const boundKey = existing.options.applicationServerKey;
    const actual = boundKey ? new Uint8Array(boundKey) : null;
    if (
      actual?.length === expected.length &&
      actual.every((byte, index) => byte === expected[index])
    ) {
      return existing;
    }
    await existing.unsubscribe();
    if (await manager.getSubscription()) {
      throw new Error("Could not retire the previous notification subscription");
    }
  }
  return manager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: expected.buffer,
  });
}
