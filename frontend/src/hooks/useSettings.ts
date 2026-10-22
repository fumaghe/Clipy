import { useQuery } from "@tanstack/react-query";
import { get } from "../lib/api";

export interface SettingsPayload {
  settings: any;
  secrets: Record<string, boolean>;
  version: number;
  live_confirm_phrase: string;
  redirect_uri: string;
}

export const useSettings = () => useQuery({ queryKey: ["settings"], queryFn: () => get<SettingsPayload>("/api/v1/settings") });
