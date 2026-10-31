import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { MediaPage, type MediaJob } from "../components/MediaPage";

const image = "data:image/png;base64,iVBORw0KGgo=";
const base: MediaJob = { id: "123", kind: "video", state: "review", prompt: "A gentle camera move", model: "gen4_turbo", license: "Runway terms", location: "Runway cloud", progress: 0, message: "", provider_id: null, output: false, cost_cents: 25, duration: 5, ratio: "1280:720", created_at: "2026-10-31" };
let jobs: MediaJob[];
let saved: boolean;
let credential: string;
const catalog = () => ({ images: { enabled: false, runtime_ready: false, installed: false, reason: "Experimental image quality did not pass review.", requirements: "Optional CUDA runtime required. No cloud fallback.", download_bytes: 4465653694 }, credential, jobs: jobs.map(job => ({ ...job })), reserved_cents: 0, limit_cents: 5000, pricing_date: "2026-10-31" });
beforeEach(() => {
  jobs = [{ ...base }]; saved = false; credential = "saved";
  vi.clearAllMocks();
  vi.mocked(invoke).mockImplementation(async (command, args) => {
    if (command === "export_media") return saved;
    const { operation, payload } = args as { operation: string; payload: Record<string, unknown> };
    if (operation === "status") return catalog();
    if (operation === "preview") return { image };
    if (operation === "submit") { jobs[0].state = "submitting"; return jobs[0]; }
    if (operation === "cancel") { jobs[0].state = "cancelled"; return jobs[0]; }
    if (operation === "credential") { credential = payload.token ? "saved" : "missing"; return { credential }; }
    if (operation === "resume") { jobs[0].state = "queued"; return jobs[0]; }
    throw new Error("Unexpected operation");
  });
});
afterEach(cleanup);
const operations = () => vi.mocked(invoke).mock.calls.filter(call => call[0] === "media_request").map(call => (call[1] as { operation: string }).operation);

it("opening Media never downloads models or submits a paid request", async () => {
  render(<MediaPage />);
  await screen.findByText("Experimental image quality did not pass review.");
  expect(operations()).toEqual(["status"]);
  expect(screen.queryByRole("button", { name: "Generate on this device" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Download image model" })).toBeNull();
});

it("reviews the persisted source, prompt, destination and price before explicit paid confirmation", async () => {
  render(<MediaPage />);
  fireEvent.click(await screen.findByRole("button", { name: /^Review$/ }));
  const review = await screen.findByRole("region", { name: "Review before sending" });
  const send = within(review).getByRole("button", { name: "Send to Runway · $0.25" }) as HTMLButtonElement;
  expect(send.disabled).toBe(true);
  expect(within(review).getByRole("img").getAttribute("src")).toBe(image);
  expect(within(review).getByText("A gentle camera move")).toBeTruthy();
  expect(operations()).not.toContain("submit");
  fireEvent.click(within(review).getByRole("checkbox"));
  fireEvent.click(send);
  fireEvent.click(send);
  await screen.findByText("Submission recorded. Follow its status in your jobs below.");
  expect(operations().filter(op => op === "submit")).toHaveLength(1);
  expect(screen.queryByRole("region", { name: "Review before sending" })).toBeNull();
});

it("dismisses a review without submitting", async () => {
  render(<MediaPage />);
  fireEvent.click(await screen.findByRole("button", { name: /^Review$/ }));
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss review" }));
  await screen.findByText("cancelled");
  expect(operations()).not.toContain("submit");
});

it("does not offer resubmission for an unknown billing outcome", async () => {
  jobs[0].state = "unknown"; jobs[0].message = "Runway may have accepted this request.";
  render(<MediaPage />);
  await screen.findByText(/Do not create a replacement/);
  expect(screen.queryByRole("button", { name: "Resume status checks" })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Review$/ })).toBeNull();
  expect(operations()).toEqual(["status"]);
});

it("resumes a known task without submitting generation", async () => {
  jobs[0].state = "paused"; jobs[0].provider_id = "provider-task";
  render(<MediaPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Resume status checks" }));
  await screen.findByText("queued");
  expect(operations()).toContain("resume");
  expect(operations()).not.toContain("submit");
});

it("cancelled export does not claim success or alter the job", async () => {
  jobs[0] = { ...base, kind: "image", state: "completed", output: true };
  render(<MediaPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Save image" }));
  await waitFor(() => expect(vi.mocked(invoke).mock.calls.some(call => call[0] === "export_media")).toBe(true));
  expect(screen.queryByText("Generated media saved to your selected location.")).toBeNull();
  expect(jobs[0].state).toBe("completed");
});

it("successful export reports the selected save and retains provenance", async () => {
  jobs[0] = { ...base, kind: "image", state: "completed", output: true, model: "segmind/SSD-1B", license: "Apache-2.0" }; saved = true;
  render(<MediaPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Save image" }));
  await screen.findByText("Generated media saved to your selected location.");
  expect(screen.getByText("segmind/SSD-1B · Apache-2.0 · Generated")).toBeTruthy();
});

it("clears the credential input and never persists the key to browser storage", async () => {
  render(<MediaPage />);
  const input = await screen.findByLabelText("Runway API key") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "private-example-key" } });
  fireEvent.click(screen.getByRole("button", { name: "Save key" }));
  await waitFor(() => expect(input.value).toBe(""));
  expect(JSON.stringify(localStorage)).not.toContain("private-example-key");
  expect(screen.queryByText("private-example-key")).toBeNull();
});

it("blocks non PNG input before preparing any review", async () => {
  jobs = [];
  render(<MediaPage />);
  const input = await screen.findByLabelText("Starting image");
  fireEvent.change(input, { target: { files: [new File(["executable"], "bad.exe", { type: "application/octet-stream" })] } });
  await screen.findByRole("alert");
  expect(operations()).not.toContain("prepare");
});
