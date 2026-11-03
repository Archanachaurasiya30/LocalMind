import { useCallback, useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

export interface ModelReadiness {
  model: string;
  preset: string;
  ready: boolean;
  state: "ready" | "needs_setup" | "offline";
  message: string;
  notice?: string | null;
  settings_notice?: string | null;
  digest?: string;
  context_window?: number;
  response_tokens?: number;
}

export function useModelReadiness(model: string, preset: string, enabled: boolean) {
  const [result, setResult] = useState<{ id: number; value: ModelReadiness } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const key = JSON.stringify([model, preset, enabled, attempt]);
  const [request, setRequest] = useState({ key, id: 0 });
  // Revisiting A after switching A -> B -> A must not reuse A's old approval.
  // Derive a new request identity before children can submit a turn.
  if (request.key !== key) setRequest({ key, id: request.id + 1 });
  const retry = useCallback(() => setAttempt(value => value + 1), []);
  useEffect(() => {
    if (!enabled || request.key !== key) return;
    let disposed = false;
    invoke<ModelReadiness>("model_readiness", { model, preset }).then(value => {
      if (!disposed) setResult({ id: request.id, value });
    }).catch(() => {
      if (!disposed) setResult({ id: request.id, value: { model, preset, ready: false, state: "offline",
        message: "Could not check this model. Retry when the local service is available." } });
    });
    return () => { disposed = true; };
  }, [enabled, model, preset, key, request.key, request.id]);
  const current = enabled && request.key === key && result?.id === request.id ? result.value : null;
  return { result: current, checking: enabled && !current, ready: current?.ready === true, retry };
}
