import { useCallback, useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { providerError, providerRequest, type ProviderStatus } from "../lib/providers";

export function useProviders() {
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  const version = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++version.current;
    try {
      const result = await providerRequest<ProviderStatus>("GET", "/providers");
      if (mounted.current && current === version.current) {
        setStatus(result && Array.isArray(result.providers) ? result : null);
        setError("");
      }
      return result;
    } catch (cause) {
      if (mounted.current && current === version.current) {
        setStatus(null); setError(providerError(cause));
      }
      return null;
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    const initial = setTimeout(() => { void refresh(); }, 0);
    const update = () => { void refresh(); };
    window.addEventListener("localmind-providers-changed", update);
    let off: (() => void) | undefined;
    void listen("sidecar-ready", update).then(unlisten => {
      if (mounted.current) off = unlisten; else unlisten();
    });
    return () => {
      mounted.current = false; clearTimeout(initial);
      window.removeEventListener("localmind-providers-changed", update); off?.();
    };
  }, [refresh]);
  return { status, error, refresh };
}
