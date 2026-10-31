import { useEffect, useId, type RefObject } from "react";
import { ArrowUp, BookOpen, Brain, Globe, Mic, ShieldCheck, Square } from "lucide-react";
import { ChatTools } from "./ChatTools";
import { ModelPicker } from "./ModelPicker";
import type { Message, ModelMode } from "../types";
import { useVoice } from "../hooks/useVoice";
import { VoiceControls } from "./VoiceControls";
import "./composer-controls.css";
import { useSpotlight } from "../hooks/useSpotlight";

interface ComposerProps {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  value: string;
  onChange: (value: string) => void;
  onSend: (text: string, turnId?: string) => Promise<boolean>;
  messages?: Message[];
  voiceScope?: string;
  onStop: () => void;
  isStreaming: boolean;
  disabled: boolean;
  memoryEnabled: boolean;
  onMemoryToggle: () => void;
  knowledgeEnabled: boolean;
  onKnowledgeToggle: () => void;
  webSearchEnabled: boolean;
  onWebSearchToggle: () => void;
  modelMode: ModelMode;
  onModelModeChange: (mode: ModelMode) => void;
  modelName: string;
  modelNames?: Partial<Record<ModelMode, string>>;
  onOpenKnowledge: () => void;
}

export function Composer(props: ComposerProps) {
  const spotlight = useSpotlight<HTMLDivElement>();
  const helpId = useId();
  const voiceId = useId();
  const {
    textareaRef,
    value,
    onChange,
    onStop,
    isStreaming,
    disabled,
    modelMode,
    onModelModeChange,
    modelName,
  } = props;
  const voice = useVoice({ ...props, scope: props.voiceScope || "new", messages: props.messages || [] });
  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    }
  }, [value, textareaRef]);
  const send = () => {
    if (value.trim() && !disabled && !isStreaming && !voice.busy) void voice.send(value);
  };
  const stop = () => {
    voice.cancel();
    onStop();
    textareaRef.current?.focus({ preventScroll: true });
  };
  const context = [
    { name: "Memory", enabled: props.memoryEnabled, Icon: Brain },
    { name: "Knowledge", enabled: props.knowledgeEnabled, Icon: BookOpen },
    { name: "Web", enabled: props.webSearchEnabled, Icon: Globe },
  ].filter(item => item.enabled);
  return (
    <div className="composer-wrap">
      <div className="composer-box spotlight-surface" {...spotlight}>
        {context.length > 0 && <div className="composer-context" aria-label="Enabled context">
          <span className="context-label">Context</span>
          {context.map(({ name, Icon }) => <span className="context-chip" key={name}>
            <Icon size={12} aria-hidden="true" />{name}
          </span>)}
        </div>}
        <textarea
          ref={textareaRef}
          aria-label="Message LocalMind"
          aria-describedby={helpId}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Ask a question, explore an idea, or start a draft…"
          rows={1}
          onKeyDown={(e) => {
            if (e.key === "Escape" && isStreaming && !e.nativeEvent.isComposing) {
              e.preventDefault();
              e.stopPropagation();
              stop();
              return;
            }
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing &&
              e.keyCode !== 229
            ) {
              e.preventDefault();
              send();
            }
          }}
        />
        <div className="composer-toolbar">
          <div className="composer-options">
            <ChatTools {...props} />
            <span className="composer-divider" aria-hidden="true" />
            <ModelPicker value={modelMode} onChange={onModelModeChange} modelName={modelName}
              models={props.modelNames} disabled={isStreaming || voice.busy} />
          </div>
          <div className="composer-send">
            <button type="button" className={`composer-voice ${voice.open ? "is-open" : ""}`}
              aria-label="Voice controls" aria-expanded={voice.open} aria-controls={voice.open ? voiceId : undefined}
              title="Voice controls" onClick={() => voice.setOpen(!voice.open)}><Mic size={18} aria-hidden="true" /></button>
            {isStreaming ? (
              <button
                type="button"
                className="send-button stop-button"
                onClick={stop}
                aria-label="Stop generation"
                title="Stop generation (Escape in message field)"
              >
                <Square size={14} fill="currentColor" aria-hidden="true" />
              </button>
            ) : (
              <button
                type="button"
                className="send-button"
                onClick={send}
                disabled={disabled || !value.trim() || voice.busy}
                aria-label="Send message"
                title="Send message (Enter)"
              >
                <ArrowUp size={19} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </div>
      <VoiceControls voice={voice} disabled={disabled} hideTrigger panelId={voiceId} />
      <div className="composer-caption" id={helpId}>
        <span>
          <ShieldCheck size={12} aria-hidden="true" />
          {props.webSearchEnabled
            ? "Web is available when needed. Search queries leave this device."
            : "Local model. Check important details."}
        </span>
        <span className="keyboard-hint">{isStreaming ? "Esc to stop" : "Shift + Enter for a new line"}</span>
      </div>
    </div>
  );
}
