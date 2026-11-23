import { useCallback } from "react";
import { useAuth } from "@clerk/react-router";
import { refusalMessage, refusalProblems } from "~/sites/team/lib/showing-bookings";
import { refusalFields } from "~/sites/team/lib/showing-team-actions";

const API_BASE = "/api/showing-bookings";

// A refusal carries the server's message, for a failed check its list of reasons, and for a
// refused form the line to show beside each field.
export type ApiResult<T> =
  | { ok: true; body: T }
  | { ok: false; status: number; message: string; problems: string[]; fields: Record<string, string> };

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
          fields: refusalFields(body),
        };
      } catch (error) {
        const message = error instanceof Error ? error.message : "Network error";
        return { ok: false, status: 0, message: refusalMessage(0, null, message), problems: [], fields: {} };
      }
    },
    [getToken],
  );
}
