import { useEffect, useRef, useState } from "react";
import { providerError, providerRequest, SCOPE_LABELS, type CloudProvider, type CloudScope, type ProviderStatus } from "../lib/providers";
import "./providers.css";

export interface CloudPreview {
  provider: string; model: string; scopes: CloudScope[]; messages: unknown[];
  cost_estimate?: { upper_bound_usd: number | null; input_bound: number; output_limit: number; pricing_known: boolean };
}
function previewImages(messages: unknown[]): string[] {
  return messages.flatMap(message => {
    if (!message || typeof message !== "object" || !("images" in message) || !Array.isArray(message.images)) return [];
    return message.images.flatMap(image => image && typeof image === "object"
      && ["image/png", "image/jpeg", "image/webp"].includes(image.mime_type) && typeof image.data === "string"
      ? [`data:${image.mime_type};base64,${image.data}`] : []);
  }).slice(0, 8);
}
export function CloudConsent({ provider, request, onCancel, onApprove }: {
  provider: CloudProvider; request: Record<string, unknown>;
  onCancel: () => void; onApprove: (consentId: string) => void;
}) {
  const [preview, setPreview] = useState<CloudPreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    let current = true;
    providerRequest<CloudPreview>("POST", "/chat/preview", request).then(result => {
      if (current) setPreview(result);
    }).catch(cause => { if (current) setError(providerError(cause)); });
    return () => { current = false; };
  }, [request, attempt]);
  useEffect(() => {
    const previous = document.activeElement;
    root.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => { if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  const approve = async () => {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      await providerRequest("PUT", `/providers/${provider.id}/consent`, { scopes: preview.scopes });
      const status = await providerRequest<ProviderStatus>("GET", "/providers");
      const consent = status.providers.find(item => item.id === provider.id);
      if (!consent?.consent_id || consent.consent_model !== provider.model) throw new Error("The model settings changed. Close this review and try again.");
      onApprove(consent.consent_id);
    } catch (cause) { setError(providerError(cause)); setBusy(false); }
  };
  return <div className="cloud-review"><section ref={root} role="dialog" aria-modal="true" aria-label="Review cloud request" onKeyDown={event => {
    if (event.key === "Escape" && !busy) onCancel();
    if (event.key === "Tab") {
      const buttons = [...(root.current?.querySelectorAll<HTMLElement>("button:not(:disabled), summary") ?? [])];
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }}>
    <h2>Send this chat to {provider.label}?</h2>
    <p>Model: <strong>{provider.model}</strong>. Your API account may be charged. No cloud generation has started.</p>
    {preview ? <>
      <p>This request includes:</p><ul>{preview.scopes.map(scope => <li key={scope}>{SCOPE_LABELS[scope]}</li>)}</ul>
      <p>{preview.cost_estimate?.upper_bound_usd != null
        ? `Conservative cost estimate: up to $${preview.cost_estimate.upper_bound_usd.toFixed(4)} using your configured prices. Your provider’s bill may differ.`
        : "Cost unknown. Configure the model’s token prices in Settings to see an estimate."}</p>
      <details><summary>Review the prepared messages and images</summary>
        {previewImages(preview.messages).map((url, index) => <img className="attachment-preview-image" src={url} alt={`Image ${index + 1} included in this request`} key={index} />)}
        <pre>{JSON.stringify(preview.messages, (key, value) => typeof value === "string" && (key === "data" || value.startsWith("data:image/")) ? "[Image bytes omitted; preview shown above]" : value, 2)}</pre>
      </details>
      <p className="page-footnote">Retrieved content can change before sending. This preview shows what was prepared now.</p>
      <p>Allow these categories for this model and provider. LocalMind will ask again if the destination, model or included categories change. Revoke permission in Settings → Cloud models.</p>
    </> : !error && <p role="status">Preparing a local preview…</p>}
    {error && <p role="alert">{error}</p>}
    <div className="provider-buttons"><button className="secondary-button" disabled={busy} onClick={onCancel}>Keep my draft</button>
      {!preview && error && <button className="secondary-button" onClick={() => { setError(""); setAttempt(value => value + 1); }}>Retry preview</button>}
      <button className="primary-button" disabled={!preview || busy} onClick={() => void approve()}>{busy ? "Saving permission…" : "Allow and send"}</button></div>
  </section></div>;
}
