import crypto from "node:crypto";

export function normalizeAudience(value?: string): string | undefined {
  const normalized = value?.trim();
  return normalized || undefined;
}

export function canonicalScopeValue(value: string): string {
  return String(value ?? "").normalize("NFKC").trim();
}

export function storageSlug(value: string): string {
  const normalized = canonicalScopeValue(value)
    .toLocaleLowerCase()
    .replace(/[^\p{Letter}\p{Number}]+/gu, "-")
    .replace(/^-+|-+$/gu, "");
  return normalized || crypto.createHash("sha256").update(value).digest("hex").slice(0, 12);
}

export function scopeStorageKey(value: string): string {
  const canonical = canonicalScopeValue(value);
  const readable = storageSlug(canonical);
  const digest = crypto.createHash("sha256").update(canonical).digest("hex").slice(0, 12);
  return `${readable}--${digest}`;
}

export function scopeValuesEqual(left?: string, right?: string): boolean {
  return canonicalScopeValue(left ?? "") === canonicalScopeValue(right ?? "");
}
