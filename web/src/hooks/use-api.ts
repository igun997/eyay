"use client";
import useSWR, { mutate as globalMutate } from "swr";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T = unknown>(url: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { ...(init?.json !== undefined ? { "Content-Type": "application/json" } : {}), ...init?.headers },
    body: init?.json !== undefined ? JSON.stringify(init.json) : init?.body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401 && typeof window !== "undefined") window.location.href = "/login";
    throw new ApiError(res.status, data.error ?? res.statusText);
  }
  return data as T;
}

/** Polling GET with stale data kept on screen (drives the "Connection lost" banner via `offline`). */
export function useApi<T>(url: string | null, refreshMs = 5000) {
  const r = useSWR<T>(url, (u: string) => api<T>(u), { refreshInterval: refreshMs, keepPreviousData: true });
  return { data: r.data, error: r.error as ApiError | undefined, loading: r.isLoading, refresh: r.mutate, offline: !!r.error && !!r.data };
}

export const revalidate = (prefix: string) => globalMutate((k) => typeof k === "string" && k.startsWith(prefix));
