import { invoke } from "@tauri-apps/api/core";

export interface ChatAttachment {
  id: string;
  filename: string;
  mime_type: string;
  width: number;
  height: number;
  bytes: number;
  data_url?: string;
  kind?: "document";
  pages?: { page: number; text: string; truncated: boolean; scanned: boolean }[];
  limitation?: string;
}
export interface AttachmentDraft extends Partial<ChatAttachment> {
  localId: string;
  readMode?: "vision" | "ocr";
  filename: string;
  status: "reading" | "ready" | "error";
  error?: string;
  file?: File;
}
export function attachmentRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  return invoke<T>("attachment_request", { method, path, body: body ?? null });
}
export function readImage(file: File): Promise<string> {
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) && !/\.(pdf|txt|md|csv)$/i.test(file.name)) {
    return Promise.reject(new Error("Choose a PNG, JPEG, WebP, PDF, TXT, Markdown or CSV file."));
  }
  if (!file.size || file.size > 8 * 1024 * 1024) return Promise.reject(new Error("Images must be between 1 byte and 8 MiB."));
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("Couldn't read this image. Choose it again."));
    reader.onabort = () => reject(new Error("Image reading was cancelled."));
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result.split(",")[1] : "";
      if (result) resolve(result); else reject(new Error("Couldn't read this image."));
    };
    reader.readAsDataURL(file);
  });
}
