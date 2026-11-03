import type { ResponseDiagnostics } from "../types";
import { CopyButton } from "./AnswerContent";

export function ResponseDetails({ data }: { data: ResponseDiagnostics }) {
  const seconds = (value: number | null) => value == null ? "Unavailable" : `${value.toFixed(2)} s`;
  const trimmed = data.history_dropped + data.references_dropped + data.references_shortened > 0;
  return <details className="response-details">
    <summary>Response details{trimmed ? " · some context did not fit" : ""}</summary>
    <dl>
      <dt>Model</dt><dd>{data.model}</dd>
      <dt>Preset</dt><dd>{data.preset || "Default"}</dd>
      <dt>First answer text</dt><dd>{seconds(data.first_token_seconds)}</dd>
      <dt>Generation wait and response</dt><dd>{seconds(data.total_seconds)}</dd>
      <dt>Output tokens</dt><dd>{data.output_tokens ?? "Unavailable"}</dd>
      <dt>Generation speed</dt><dd>{data.tokens_per_second == null ? "Unavailable" : `${data.tokens_per_second} tokens/s`}</dd>
      <dt>Context / response limit</dt><dd>{data.context_window} / {data.response_tokens} tokens</dd>
      <dt>Omitted history messages</dt><dd>{data.history_dropped}</dd>
      <dt>Omitted / shortened references</dt><dd>{data.references_dropped} / {data.references_shortened}</dd>
      <dt>Stop reason</dt><dd>{data.stop_reason}</dd>
      {data.digest && <><dt>Model fingerprint</dt><dd className="response-fingerprint">{data.digest}</dd></>}
    </dl>
    <p>Measurements describe this response, not its correctness. Details are kept for this session. Copied diagnostics contain no prompt or answer text.</p>
    <CopyButton text={JSON.stringify(data, null, 2)} label="Copy diagnostics" />
  </details>;
}
