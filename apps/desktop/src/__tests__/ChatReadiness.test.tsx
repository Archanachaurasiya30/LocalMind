import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ChatReadiness } from "../components/ChatReadiness";
import type { ComponentProps } from "react";

afterEach(cleanup);
function props(overrides: Partial<ComponentProps<typeof ChatReadiness>> = {}): ComponentProps<typeof ChatReadiness> {
  return {
    runtimeStatus: "ready", runtimeError: null, ollamaRunning: true,
    modelAvailable: true, model: "test:8b", checking: false,
    result: { model: "test:8b", preset: "balanced", ready: true, state: "ready", message: "Ready" },
    onRestart: vi.fn(), onRetry: vi.fn(), onSettings: vi.fn(), onDownloads: vi.fn(),
    ...overrides,
  };
}

it("prioritizes runtime failure over a cached model approval and offers only restart", () => {
  const p = props({ runtimeStatus: "failed", runtimeError: "Connection refused" });
  render(<ChatReadiness {...p} />);
  expect(screen.getByRole("status").textContent).toContain("couldn't start");
  expect(screen.getAllByRole("button")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Restart service" }));
  expect(p.onRestart).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByText("Technical details"));
  expect(screen.getByText("Connection refused").closest("details")?.open).toBe(true);
});

it("keeps startup and model inspection free of irrelevant download actions", () => {
  const p = props({ runtimeStatus: "starting", result: null });
  const { rerender } = render(<ChatReadiness {...p} />);
  expect(screen.getByRole("status").textContent).toContain("Starting");
  expect(screen.queryByRole("button")).toBeNull();
  rerender(<ChatReadiness {...p} runtimeStatus="ready" checking />);
  expect(screen.getByRole("status").textContent).toContain("Verifying that test:8b supports chat");
  expect(screen.getByRole("status").textContent).not.toContain("Download");
  expect(screen.queryByRole("button")).toBeNull();
});

it("routes an unavailable model service to settings instead of a futile model check", () => {
  const p = props({ ollamaRunning: false, modelAvailable: false });
  render(<ChatReadiness {...p} />);
  fireEvent.click(screen.getByRole("button", { name: "Open settings" }));
  expect(p.onSettings).toHaveBeenCalledOnce();
  expect(screen.getAllByRole("button")).toHaveLength(1);
});

it("offers downloads for missing weights even when an older check reported ready", () => {
  const p = props({ modelAvailable: false });
  render(<ChatReadiness {...p} />);
  expect(screen.getByRole("status").textContent).toContain("test:8b");
  fireEvent.click(screen.getByRole("button", { name: "Manage model downloads" }));
  expect(p.onDownloads).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "Check model again" })).toBeNull();
});

it("retains the inspection failure and connects retry to the current model", () => {
  const p = props({ result: { model: "test:8b", preset: "balanced", ready: false, state: "offline", message: "Inspection timed out." } });
  render(<ChatReadiness {...p} />);
  expect(screen.getByRole("status").textContent).toContain("Inspection timed out.");
  fireEvent.click(screen.getByRole("button", { name: "Check model again" }));
  expect(p.onRetry).toHaveBeenCalledOnce();
});

it("keeps the model limitation visible when settings also need explanation", () => {
  const p = props();
  render(<ChatReadiness {...p} result={{ ...p.result!, notice: "This compact model can miss instructions.", settings_notice: "The context limit is 4096 tokens." }} />);
  expect(screen.getByRole("note").textContent).toContain("miss instructions");
  expect(screen.queryByRole("region", { name: "Chat availability" })).toBeNull();
  fireEvent.click(screen.getByText("Model settings"));
  expect(screen.getByText("The context limit is 4096 tokens.").closest("details")?.open).toBe(true);
});

it("renders nothing when chat is ready without notices", () => {
  const { container } = render(<ChatReadiness {...props()} />);
  expect(container.childElementCount).toBe(0);
});
