import { useCallback } from "react";
import { useAuth } from "@clerk/react-router";
import { refusalMessage, refusalProblems } from "~/lib/showing-bookings";

const API_BASE = "/api/showing-bookings";

// A refusal carries the server's message and, for a failed check, its list of reasons.
export type ApiResult<T> = { ok: true; body: T } | { ok: false; status: number; message: string; problems: string[] };

/** Calls /api/showing-bookings with the signed-in user's token. Never throws. */
export function useShowingBookingsApi() {
  const { getToken } = useAuth();
  return useCallback(
    async <T,>(path: string, init: RequestInit = {}, fallback = "Request failed"): Promise<ApiResult<T>> => {
      try {
        const token = await getToken();
        const response = await fetch(`${API_BASE}${path}`, {
          ...init,
          headers: {
            Authorization: `Bearer ${token}`,
            ...(init.body ? { "Content-Type": "application/json" } : {}),
          },
        });
        const body = await response.json().catch(() => null);
        if (response.ok) return { ok: true, body: body as T };
        return {
          ok: false,
          status: response.status,
          message: refusalMessage(response.status, body, fallback),
          problems: refusalProblems(body),
        };
      } catch (error) {
        const message = error instanceof Error ? error.message : "Network error";
        return { ok: false, status: 0, message: refusalMessage(0, null, message), problems: [] };
      }
    },
    [getToken],
  );
}
