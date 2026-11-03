import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { FirstRunSetup } from "../components/FirstRunSetup";

beforeEach(() => { vi.clearAllMocks(); vi.mocked(listen).mockResolvedValue(() => {}); });
afterEach(cleanup);
it("changes the selected chat model only after an explicit choice and never downloads it automatically", async () => {
  vi.mocked(invoke).mockResolvedValue([]);
  const choose = vi.fn();
  render(<FirstRunSetup onComplete={vi.fn()} onChooseChatModel={choose} />);
  fireEvent.change(await screen.findByRole("combobox", { name: "Chat model" }), { target: { value: "qwen2.5:1.5b" } });
  expect(choose).toHaveBeenCalledWith("qwen2.5:1.5b");
  expect(invoke).not.toHaveBeenCalledWith("pull_ollama_model", expect.anything());
  expect(screen.getByRole("button", { name: "Download 1 model" })).toBeTruthy();
});
it("does not dismiss setup for an unrelated or embedding-only installation", async () => {
  vi.mocked(invoke).mockResolvedValue(["nomic-embed-text:latest", "qwen2.5:1.5b"]);
  const done = vi.fn();
  render(<FirstRunSetup chatModel="qwen3:8b" onComplete={done} />);
  await screen.findByText("Set up LocalMind");
  expect(done).not.toHaveBeenCalled();
  expect(screen.getAllByText("Installed")).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Download 1 model" })).toBeTruthy();
});
it("verifies the configured model before bypassing setup", async () => {
  vi.mocked(invoke).mockImplementation(async command => command === "list_ollama_models" ? ["qwen3:8b"] : { ready: true });
  const done = vi.fn();
  render(<FirstRunSetup onComplete={done} />);
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(invoke).toHaveBeenCalledWith("model_readiness", { model: "qwen3:8b", preset: "balanced" });
});
it("shows inspection failures and lets the user retry without pretending setup succeeded", async () => {
  vi.mocked(invoke).mockRejectedValueOnce(new Error("offline"));
  const done = vi.fn();
  render(<FirstRunSetup onComplete={done} />);
  await screen.findByRole("alert");
  expect(done).not.toHaveBeenCalled();
  vi.mocked(invoke).mockImplementation(async command => command === "list_ollama_models" ? ["qwen3:8b"] : { ready: true });
  fireEvent.click(screen.getByRole("button", { name: "Retry model check" }));
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
});
it("rechecks after a download and does not open chat when verification fails", async () => {
  vi.mocked(invoke).mockImplementation(async command => command === "list_ollama_models" ? [] : command === "model_readiness" ? { ready: false, message: "Model does not support chat" } : undefined);
  const done = vi.fn();
  render(<FirstRunSetup onComplete={done} />);
  fireEvent.click(await screen.findByRole("button", { name: "Download 1 model" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open LocalMind" }));
  expect((await screen.findByRole("alert")).textContent).toContain("does not support chat");
  expect(done).not.toHaveBeenCalled();
  expect(invoke).toHaveBeenCalledWith("pull_ollama_model", { name: "qwen3:8b" });
});
