import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { useModelReadiness } from "../hooks/useModelReadiness";

beforeEach(() => vi.clearAllMocks());
it("does not reuse stale readiness when switching away and back to the same model", async () => {
  vi.mocked(invoke).mockResolvedValueOnce({ ready: true, model: "a" });
  const { result, rerender } = renderHook(({ model }) => useModelReadiness(model, "balanced", true), { initialProps: { model: "a" } });
  await waitFor(() => expect(result.current.ready).toBe(true));
  vi.mocked(invoke).mockImplementation(() => new Promise(() => {}));
  rerender({ model: "b" });
  expect(result.current.ready).toBe(false);
  rerender({ model: "a" });
  expect(result.current.ready).toBe(false);
  expect(result.current.checking).toBe(true);
});
it("checks the selected model, invalidates immediately on switching, and ignores old results", async () => {
  let finishOld!: (value: unknown) => void;
  vi.mocked(invoke).mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }));
  vi.mocked(invoke).mockResolvedValue({ ready: false, state: "needs_setup", message: "Not installed" });
  const { result, rerender } = renderHook(({ model }) => useModelReadiness(model, "balanced", true), { initialProps: { model: "old" } });
  expect(result.current.ready).toBe(false);
  rerender({ model: "new" });
  await waitFor(() => expect(result.current.result?.message).toBe("Not installed"));
  await act(async () => finishOld({ ready: true, model: "old" }));
  expect(result.current.ready).toBe(false);
  expect(invoke).toHaveBeenCalledWith("model_readiness", { model: "new", preset: "balanced" });
});
it("retries failed checks and does not query before the service is ready", async () => {
  vi.mocked(invoke).mockRejectedValueOnce(new Error("offline"));
  const { result, rerender } = renderHook(({ enabled }) => useModelReadiness("chat", "speed", enabled), { initialProps: { enabled: false } });
  expect(invoke).not.toHaveBeenCalled();
  rerender({ enabled: true });
  await waitFor(() => expect(result.current.result?.state).toBe("offline"));
  vi.mocked(invoke).mockResolvedValue({ ready: true, model: "chat", state: "ready" });
  act(() => result.current.retry());
  expect(result.current.ready).toBe(false);
  await waitFor(() => expect(result.current.ready).toBe(true));
  rerender({ enabled: false });
  expect(result.current.ready).toBe(false);
});
