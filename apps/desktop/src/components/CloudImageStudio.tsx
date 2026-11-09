import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { AttachmentPreview } from "./ChatAttachments";
import type { MediaJob } from "./MediaPage";

interface ImageJob extends MediaJob { reference_id?: string; width?: number; height?: number }
const request = <T,>(operation: string, payload: unknown) => invoke<T>("media_request", { operation, payload });

export function CloudImageStudio({ jobs }: { jobs: MediaJob[] }) {
  const [prompt, setPrompt] = useState(""), [model, setModel] = useState("gpt-image-2");
  const [allowance, setAllowance] = useState(25), [reference, setReference] = useState("");
  const [review, setReview] = useState<ImageJob | null>(null), [approved, setApproved] = useState(false);
  const [referenceImage, setReferenceImage] = useState(""), [preview, setPreview] = useState<{ filename: string; data_url: string } | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  async function act(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError("");
    try { await work(); } catch (cause) { setError(String(cause)); } finally { setBusy(false); }
  }
  return <section className="media-card" aria-label="Cloud image studio"><h2>Image API</h2>
    <p>Uses your OpenAI key from Settings. Review the exact prompt and reference before anything leaves this device.</p>
    <label className="media-field">Model<select value={model} disabled={!!review} onChange={event => setModel(event.target.value)}>{["gpt-image-2", "gpt-image-1.5", "gpt-image-1-mini"].map(value => <option key={value}>{value}</option>)}</select></label>
    <label className="media-field">Prompt<textarea maxLength={1000} value={prompt} disabled={!!review} onChange={event => setPrompt(event.target.value)} /></label>
    <label className="media-field">Reference image<select value={reference} disabled={!!review} onChange={event => setReference(event.target.value)}><option value="">Generate a new image</option>{jobs.filter(job => job.output && job.state === "completed" && job.kind !== "video").map(job => <option key={job.id} value={job.id}>{job.prompt.slice(0, 70)} · {job.id.slice(0, 8)}</option>)}</select></label>
    <label className="media-field">Reserve cents for this request<input type="number" min={1} max={500} value={allowance} disabled={!!review} onChange={event => setAllowance(Number(event.target.value))} /></label>
    <p>Reservation only, not a price quote or billing cap. Check provider pricing and account limits. Unverified costs remain reserved in LocalMind.</p>
    {!review && <button className="page-button" disabled={busy || !prompt.trim()} onClick={() => void act(async () => {
      const next = await request<ImageJob>("cloud-image-prepare", { prompt, model, allowance_cents: allowance, reference_id: reference || null });
      const image = reference ? await request<{ image: string }>("preview", { job_id: reference }) : null;
      setReferenceImage(image?.image || ""); setApproved(false); setReview(next);
    })}>Review request</button>}
    {review && <section aria-label="Image request review"><h3>Send to OpenAI</h3><p>{review.model} · 1024 × 1024 · low quality</p><blockquote>{review.prompt}</blockquote>{referenceImage && <img src={referenceImage} alt="Exact reference selected for this edit" style={{ maxWidth: 240 }} />}
      <p>{review.reference_id ? "The selected image and prompt will be sent. The edit is saved separately; the original stays intact." : "Only this prompt will be sent. No conversation or files are included."}</p>
      <label><input type="checkbox" checked={approved} onChange={event => setApproved(event.target.checked)} />I approve this request and the {(review.cost_cents/100).toFixed(2)} USD reservation. Actual charges can differ.</label>
      <button disabled={busy || !approved} onClick={() => void act(async () => { await request("cloud-image-submit", { job_id: review.id }); setReview(null); setApproved(false); })}>Send once</button>
      <button disabled={busy} onClick={() => void act(async () => { await request("cancel", { job_id: review.id }); setReview(null); })}>Discard review</button></section>}
    {error && <p role="alert">{error}</p>}
    <div>{(jobs.filter(job => job.kind === "cloud-image") as ImageJob[]).map(job => <article key={job.id}><h3>{job.prompt}</h3><p>{job.model} · {job.state} · {job.created_at}{job.width ? ` · ${job.width} × ${job.height}` : ""}</p><p>{job.message}</p>
      {job.reference_id && <p>Edited from image {job.reference_id}</p>}{job.provider_id && <small>Provider request: {job.provider_id}</small>}
      {job.output && <><button onClick={() => void act(async () => { const image = await request<{ image: string }>("preview", { job_id: job.id }); setPreview({ filename: job.prompt, data_url: image.image }); })}>View image</button>
        <button onClick={() => void act(async () => { await invoke("export_media", { jobId: job.id, kind: "image" }); })}>Save image</button>
        <button onClick={() => void act(async () => {
          const image = await request<{ image: string }>("preview", { job_id: job.id });
          const bytes = Uint8Array.from(atob(image.image.split(",")[1]), character => character.charCodeAt(0));
          window.dispatchEvent(new CustomEvent("localmind-generated-image", { detail: new File([bytes], `Generated-${job.id}.png`, { type: "image/png" }) }));
        })}>Attach to chat</button>
        <button disabled={!!review} onClick={() => { setReference(job.id); setPrompt(""); }}>Edit this image</button></>}
      {["submitting", "generating"].includes(job.state) && <button onClick={() => void act(async () => { await request("cancel", { job_id: job.id }); })}>Stop waiting (charges may apply)</button>}
    </article>)}</div>
    {preview && <AttachmentPreview item={preview} onClose={() => setPreview(null)} />}
  </section>;
}
