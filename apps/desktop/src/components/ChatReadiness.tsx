import type { SidecarStatus } from "../hooks/useSidecarStatus";
import type { ModelReadiness } from "../hooks/useModelReadiness";

interface Props {
  runtimeStatus: SidecarStatus;
  runtimeError: string | null;
  ollamaRunning: boolean;
  modelAvailable: boolean;
  model: string;
  checking: boolean;
  result: ModelReadiness | null;
  onRestart: () => void;
  onRetry: () => void;
  onSettings: () => void;
  onDownloads: () => void;
}

/** Keep the blocking reason and its recovery actions together, including in mini chat. */
export function ChatReadiness({
  runtimeStatus, runtimeError, ollamaRunning, modelAvailable, model, checking,
  result, onRestart, onRetry, onSettings, onDownloads,
}: Props) {
  let title: string;
  let description: string;
  let actions: { label: string; run: () => void }[] = [];

  if (runtimeStatus === "failed") {
    title = "LocalMind couldn't start";
    description = "Restart the local service to reconnect. Your saved data stays on this device.";
    actions = [{ label: "Restart service", run: onRestart }];
  } else if (runtimeStatus === "starting") {
    title = "Starting the local service…";
    description = "You can write a message while the local service gets ready.";
  } else if (!ollamaRunning) {
    title = "Waiting for the model service";
    description = "Check that Ollama is running, then return to this chat.";
    actions = [{ label: "Open settings", run: onSettings }];
  } else if (!modelAvailable) {
    title = "Your selected model isn't installed";
    description = `Download ${model}, or choose an installed model in Settings.`;
    actions = [{ label: "Manage model downloads", run: onDownloads }, { label: "Open settings", run: onSettings }];
  } else if (checking) {
    title = "Checking your selected model…";
    description = `Verifying that ${model} supports chat. Your draft stays here.`;
  } else if (!result?.ready) {
    title = "Your model needs attention";
    description = result?.message || "Check your selected model before sending a message.";
    actions = [{ label: "Check model again", run: onRetry }, { label: "Open settings", run: onSettings }, { label: "Manage model downloads", run: onDownloads }];
  } else {
    return result.notice || result.settings_notice ? (
      <div className="model-quality-notice">
        {result.notice && <p role="note">{result.notice}</p>}
        {result.settings_notice && result.settings_notice !== result.notice && (
          <details><summary>Model settings</summary><p>{result.settings_notice}</p></details>
        )}
      </div>
    ) : null;
  }

  return (
    <section className="runtime-banner" aria-label="Chat availability">
      <div>
        <div role="status" aria-atomic="true">
          <strong>{title}</strong>
          <p>{description}</p>
        </div>
        {runtimeStatus === "failed" && runtimeError && (
          <details><summary>Technical details</summary><pre>{runtimeError}</pre></details>
        )}
        {actions.length > 0 && (
          <div className="model-readiness-actions">
            {actions.map(({ label, run }) => <button type="button" className="secondary-button" key={label} onClick={run}>{label}</button>)}
          </div>
        )}
      </div>
    </section>
  );
}
