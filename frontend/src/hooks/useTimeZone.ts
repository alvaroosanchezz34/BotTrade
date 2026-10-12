import { useSyncExternalStore } from "react";
import { getTimeZone } from "../services/format";

function subscribe(cb: () => void): () => void {
  window.addEventListener("bottrade:tz", cb);
  return () => window.removeEventListener("bottrade:tz", cb);
}

export function useTimeZone(): string {
  return useSyncExternalStore(subscribe, getTimeZone, () => "UTC");
}
