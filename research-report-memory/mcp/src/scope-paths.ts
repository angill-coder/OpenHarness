import crypto from "node:crypto";

export function normalizeAudience(value?: string): string | undefined {
  const normalized = value?.trim();
  return normalized || undefined;
}

export function storageSlug(value: string): string {
  const normalized = value
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[^\p{Letter}\p{Number}]+/gu, "-")
    .replace(/^-+|-+$/gu, "");
  return normalized || crypto.createHash("sha256").update(value).digest("hex").slice(0, 12);
}
