import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useToast } from "../components/ui";
import { get } from "../lib/api";
import type { SystemStatus } from "../types";

export const useStatus = () => useQuery({ queryKey: ["status"], queryFn: () => get<SystemStatus>("/api/v1/system/status"), refetchInterval: 4000 });

/** Mutazione con toast e invalidazione delle query indicate (default: tutto). */
export function useAction<TArgs = void, TRes = any>(fn: (a: TArgs) => Promise<TRes>, opts?: { ok?: string | ((r: TRes) => string); invalidate?: string[][] }) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: fn,
    onSuccess: (r) => {
      const msg = typeof opts?.ok === "function" ? opts.ok(r) : opts?.ok;
      if (msg) toast(msg);
      if (opts?.invalidate) opts.invalidate.forEach((k) => qc.invalidateQueries({ queryKey: k }));
      else qc.invalidateQueries();
    },
    onError: (e: unknown) => toast(e instanceof Error ? e.message : "Errore", "err"),
  });
}
