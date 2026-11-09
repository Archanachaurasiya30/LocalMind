import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { attachmentRequest, readImage, type AttachmentDraft, type ChatAttachment } from "../lib/attachments";
import { providerError } from "../lib/providers";

interface Draft { id: string; items: AttachmentDraft[] }
export function useAttachments(scope: string) {
  const drafts = useRef<Record<string, Draft>>({});
  const currentScope = useRef(scope);
  useLayoutEffect(() => { currentScope.current = scope; }, [scope]);
  const mounted = useRef(true);
  const [visible, update] = useState<Record<string, Draft>>({});
  const [notice, setNotice] = useState("");
  const [mode, setMode] = useState<"vision" | "ocr">("vision");
  const draft = () => drafts.current[scope] ?? (drafts.current[scope] = { id: crypto.randomUUID(), items: [] });
  const refresh = () => { if (mounted.current) update({ ...drafts.current }); };
  const discard = (id: string, draftId: string) => void attachmentRequest("DELETE", `/attachments/${id}`, { draft_id: draftId }).catch(() => {});
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const upload = async (file: File, item: AttachmentDraft, owner: Draft, key: string) => {
    try {
      const data = await readImage(file);
      if (!mounted.current || currentScope.current !== key || !owner.items.includes(item)) return;
      const uploaded = await attachmentRequest<ChatAttachment>("POST", "/attachments", {
        filename: file.name || "Screenshot.png", data, draft_id: owner.id,
        mode: item.readMode || "vision",
        conversation_id: key === "new" ? null : key,
      });
      if (!mounted.current || currentScope.current !== key || !owner.items.includes(item)) {
        discard(uploaded.id, owner.id);
        owner.items = owner.items.filter(entry => entry !== item);
      } else Object.assign(item, uploaded, { status: "ready", file: undefined });
    } catch (cause) {
      if (owner.items.includes(item)) Object.assign(item, { status: "error", error: providerError(cause) });
    } finally {
      // An unfinished read belongs to its original draft even when navigation changed.
      if (currentScope.current !== key && item.status === "reading") owner.items = owner.items.filter(entry => entry !== item);
      refresh();
    }
  };
  const add = (files: File[]) => {
    const owner = draft();
    const available = Math.max(0, 4 - owner.items.length);
    setNotice(files.length > available ? "Attach up to four images per message. Remove one before adding another." : "");
    files.slice(0, available).forEach(file => {
      const item: AttachmentDraft = { localId: crypto.randomUUID(), filename: file.name || "Screenshot.png", status: "reading", file, readMode: file.type.startsWith("image/") ? mode : "vision" };
      owner.items.push(item);
      void upload(file, item, owner, scope);
    });
    refresh();
  };
  const remove = (localId: string) => {
    const owner = draft();
    const item = owner.items.find(entry => entry.localId === localId);
    owner.items = owner.items.filter(entry => entry !== item);
    if (item?.id) discard(item.id, owner.id);
    refresh();
  };
  const retry = (localId: string) => {
    const owner = draft(), item = owner.items.find(entry => entry.localId === localId);
    if (!item?.file || item.status !== "error") return;
    item.status = "reading"; item.error = undefined;
    void upload(item.file, item, owner, scope); refresh();
  };
  const release = useCallback((key: string, ids: string[]) => {
    const owner = drafts.current[key];
    if (owner) owner.items = owner.items.filter(item => !item.id || !ids.includes(item.id));
    update({ ...drafts.current });
  }, []);
  const move = useCallback((from: string, to: string) => {
    if (from === to || !drafts.current[from]) return;
    drafts.current[to] = drafts.current[from]; delete drafts.current[from];
    update({ ...drafts.current });
  }, []);
  const restore = useCallback((key: string, id: string, items: ChatAttachment[]) => {
    const existing = drafts.current[key];
    // A newer draft may already contain images owned by another draft ID.
    // Keep those edits; the failed message still has its own explicit Retry.
    if (existing?.items.length && existing.id !== id) return;
    const owner = existing?.id === id ? existing : { id, items: [] };
    for (const item of items) {
      if (!owner.items.some(candidate => candidate.id === item.id)) owner.items.push({ ...item, localId: crypto.randomUUID(), status: "ready" });
    }
    drafts.current[key] = owner;
    if (mounted.current) update({ ...drafts.current });
  }, []);
  const owner = visible[scope] ?? { id: undefined, items: [] };
  return { items: owner.items, draftId: owner.id, add, remove, retry, release, move, restore, notice, mode, setMode,
    busy: owner.items.some(item => item.status === "reading"),
    valid: owner.items.every(item => item.status === "ready"),
    attachments: owner.items.filter(item => item.status === "ready") as (AttachmentDraft & ChatAttachment)[] };
}
