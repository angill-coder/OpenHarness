import crypto from "node:crypto";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { normalizeAudience, storageSlug } from "./scope-paths.ts";
import type { WritingMemoryScope } from "./runtime.ts";
import { L2B_RUBRICS_DIR } from "./storage-layout.ts";

const execFileAsync = promisify(execFile);

export const RUBRIC_DIMENSIONS = [
  "traceability",
  "structure",
  "narrative",
  "insight",
  "coverage",
  "expression",
  "personal",
] as const;

export type RubricDimension = (typeof RUBRIC_DIMENSIONS)[number];

export const RUBRIC_OPERATIONS = ["add", "extend", "override", "disable"] as const;
export type RubricOperation = (typeof RUBRIC_OPERATIONS)[number];

export interface RubricRequirement {
  key: string;
  text: string;
}

export interface RubricOptimizer {
  pattern_id: string;
  directive_hint: string;
  priority: number;
}

export interface RubricItem {
  id: string;
  criterionKey: string;
  operation: RubricOperation;
  dimension: RubricDimension;
  label: string;
  desc: string;
  effect: string;
  requirements?: RubricRequirement[];
  redline: false;
  optimizer?: RubricOptimizer;
  status: "active";
  sourceL1Ids: string[];
}

export interface RubricPatch {
  scope: WritingMemoryScope;
  scopeValue?: string;
  upsertItems?: RubricItem[];
  removeItemIds?: string[];
}

export interface LoadedRubricDocument {
  schemaVersion: 2;
  path: string;
  scope: WritingMemoryScope;
  scopeValue?: string;
  rubrics: RubricItem[];
}

interface StoredRubricDocument {
  schemaVersion: 2;
  scope: WritingMemoryScope;
  scopeValue?: string;
  rubrics: RubricItem[];
}

export interface RubricSetManifest {
  schemaVersion: 1;
  product: "research_insight";
  version: string;
  versionNumber: number;
  parentVersion: string | null;
  baseRubricVersion: string;
  updatedAt: string;
}

function serialize(document: StoredRubricDocument): string {
  return `${JSON.stringify(document, null, 2)}\n`;
}

function renderMarkdown(manifest: RubricSetManifest, documents: StoredRubricDocument[]): string {
  const lines = [
    `# Research Report Rubric Set ${manifest.version}`,
    "",
    `- Base Rubric: ${manifest.baseRubricVersion}`,
    `- Parent: ${manifest.parentVersion ?? "none"}`,
    `- Updated: ${manifest.updatedAt}`,
    "",
    "> 此文件由 Rubric Repository 自动生成，供人和 Memory Agent 审查；Judge 以 JSON 与运行时冻结结果为准。",
  ];
  for (const document of documents.sort((a, b) => `${a.scope}:${a.scopeValue ?? ""}`.localeCompare(`${b.scope}:${b.scopeValue ?? ""}`))) {
    lines.push("", `## ${document.scope}${document.scopeValue ? ` · ${document.scopeValue}` : ""}`);
    if (document.rubrics.length === 0) {
      lines.push("", "_No overlay items._");
      continue;
    }
    for (const item of document.rubrics) {
      lines.push(
        "",
        `### ${item.id} · ${item.label}`,
        "",
        `- Criterion: \`${item.criterionKey}\``,
        `- Operation: \`${item.operation}\``,
        `- Dimension: \`${item.dimension}\``,
        `- Standard: ${item.desc}`,
        `- Effect: ${item.effect}`,
      );
      if (item.requirements?.length) {
        lines.push("- Requirements:", ...item.requirements.map((value) => `  - \`${value.key}\`: ${value.text}`));
      }
      lines.push(`- Sources: ${item.sourceL1Ids.map((value) => `\`${value}\``).join(", ")}`);
    }
  }
  return `${lines.join("\n")}\n`;
}

export class RubricRepository {
  readonly root: string;
  private readonly worktreesRoot: string;
  private writeQueue: Promise<unknown> = Promise.resolve();

  constructor(dataDir: string) {
    this.root = path.join(dataDir, L2B_RUBRICS_DIR, "personal", "default");
    this.worktreesRoot = path.join(dataDir, L2B_RUBRICS_DIR, "worktrees");
  }

  async initialize(): Promise<void> {
    await fs.mkdir(this.root, { recursive: true, mode: 0o700 });
    await fs.mkdir(this.worktreesRoot, { recursive: true, mode: 0o700 });
    let exists = false;
    try {
      await fs.access(path.join(this.root, ".git"));
      exists = true;
    } catch { /* initialize below */ }
    if (exists) {
      await this.ensureManifest();
      return;
    }

    await this.git(["init", "-b", "main"], this.root);
    await this.git(["config", "user.name", "Research Report Memory"], this.root);
    await this.git(["config", "user.email", "memory@local.invalid"], this.root);
    await fs.mkdir(path.join(this.root, "system"), { recursive: true, mode: 0o700 });
    await fs.mkdir(path.join(this.root, ".memory"), { recursive: true, mode: 0o700 });
    await this.atomicWrite(path.join(this.root, "system", "rubrics.json"), serialize({
      schemaVersion: 2,
      scope: "core",
      rubrics: [],
    }));
    await this.atomicWrite(path.join(this.root, "manifest.json"), `${JSON.stringify({
      schemaVersion: 1,
      product: "research_insight",
      version: "v0",
      versionNumber: 0,
      parentVersion: null,
      baseRubricVersion: "v2.3",
      updatedAt: new Date().toISOString(),
    } satisfies RubricSetManifest, null, 2)}\n`);
    await fs.mkdir(path.join(this.root, "views"), { recursive: true, mode: 0o700 });
    const initialManifest = JSON.parse(await fs.readFile(path.join(this.root, "manifest.json"), "utf8")) as RubricSetManifest;
    await this.atomicWrite(path.join(this.root, "views", "rubric-set.md"), renderMarkdown(initialManifest, [{ schemaVersion: 2, scope: "core", rubrics: [] }]));
    await this.atomicWrite(path.join(this.root, ".memory", "provenance.jsonl"), "");
    await this.git(["add", "--all"], this.root);
    await this.git(["commit", "-m", "memory: initialize L2B rubric repository"], this.root);
  }

  async head(): Promise<string> {
    await this.initialize();
    return (await this.git(["rev-parse", "HEAD"], this.root)).trim();
  }

  async manifest(): Promise<RubricSetManifest> {
    await this.initialize();
    return this.readManifest("HEAD");
  }

  async recall(input: { audience?: string; project?: string }): Promise<LoadedRubricDocument[]> {
    await this.initialize();
    const paths = [
      "system/rubrics.json",
      ...(input.audience?.trim() ? [`audiences/${storageSlug(normalizeAudience(input.audience) ?? input.audience)}/rubrics.json`] : []),
      ...(input.project?.trim() ? [`projects/${storageSlug(input.project)}/rubrics.json`] : []),
    ];
    const documents = await Promise.all([...new Set(paths)].map((value) => this.readDocument(value)));
    return documents.filter((value): value is LoadedRubricDocument => Boolean(value));
  }

  async snapshot(): Promise<{ head: string; manifest: RubricSetManifest; documents: LoadedRubricDocument[] }> {
    await this.initialize();
    const paths = await this.rubricPaths(this.root);
    const documents = (await Promise.all(paths.map((value) => this.readDocument(value))))
      .filter((value): value is LoadedRubricDocument => Boolean(value));
    return { head: await this.head(), manifest: await this.manifest(), documents };
  }

  async applyPatches(patches: RubricPatch[], runId: string): Promise<{ head: string; changedPaths: string[]; rubricSetVersion: string }> {
    const operation = this.writeQueue.then(() => this.applyPatchesUnlocked(patches, runId));
    this.writeQueue = operation.catch(() => undefined);
    return operation;
  }

  async forgetItem(id: string, runId: string): Promise<{ deleted: number; head: string; changedPaths: string[]; rubricSetVersion: string }> {
    const snapshot = await this.snapshot();
    const patches: RubricPatch[] = [];
    let deleted = 0;
    for (const document of snapshot.documents) {
      if (!document.rubrics.some((item) => item.id === id)) continue;
      deleted += document.rubrics.filter((item) => item.id === id).length;
      patches.push({
        scope: document.scope,
        ...(document.scopeValue ? { scopeValue: document.scopeValue } : {}),
        removeItemIds: [id],
      });
    }
    if (patches.length === 0) return { deleted: 0, head: snapshot.head, changedPaths: [], rubricSetVersion: (await this.manifest()).version };
    const result = await this.applyPatches(patches, runId);
    return { deleted, ...result };
  }

  private async applyPatchesUnlocked(patches: RubricPatch[], runId: string) {
    await this.initialize();
    if (patches.length === 0) return { head: await this.head(), changedPaths: [], rubricSetVersion: (await this.manifest()).version };
    const baseHead = await this.head();
    const currentManifest = await this.readManifest(baseHead);
    const safeRunId = storageSlug(runId);
    const worktree = path.join(this.worktreesRoot, `${Date.now()}-${safeRunId}`);
    const changedPaths = [...new Set(patches.map((patch) => this.pathFor(patch)))];
    const documents: StoredRubricDocument[] = [];
    const existingDocuments = new Map<string, StoredRubricDocument>();
    for (const relativePath of await this.rubricPaths(this.root)) {
      const existing = await this.readDocument(relativePath);
      if (existing) existingDocuments.set(relativePath, existing);
    }

    for (const patch of patches) {
      this.validateScope(patch);
      const current = await this.readDocument(this.pathFor(patch));
      const removeIds = new Set(patch.removeItemIds ?? []);
      const upserts = new Map((patch.upsertItems ?? []).map((item) => [item.id, item]));
      const rubrics = (current?.rubrics ?? [])
        .filter((item) => !removeIds.has(item.id) && !upserts.has(item.id));
      rubrics.push(...upserts.values());
      const document: StoredRubricDocument = {
        schemaVersion: 2,
        scope: patch.scope,
        ...(patch.scopeValue ? { scopeValue: this.normalizeScopeValue(patch.scope, patch.scopeValue) } : {}),
        rubrics,
      };
      this.validateDocument(document);
      documents.push(document);
    }

    try {
      await this.git(["worktree", "add", "--detach", worktree, baseHead], this.root);
      for (const document of documents) {
        await this.atomicWrite(path.join(worktree, this.pathFor(document)), serialize(document));
      }
      const status = await this.git(["status", "--porcelain", "--", ...changedPaths], worktree);
      if (!status.trim()) return { head: baseHead, changedPaths: [], rubricSetVersion: currentManifest.version };
      const nextManifest: RubricSetManifest = {
        ...currentManifest,
        version: `v${currentManifest.versionNumber + 1}`,
        versionNumber: currentManifest.versionNumber + 1,
        parentVersion: currentManifest.version,
        updatedAt: new Date().toISOString(),
      };
      await this.atomicWrite(path.join(worktree, "manifest.json"), `${JSON.stringify(nextManifest, null, 2)}\n`);
      for (const document of documents) existingDocuments.set(this.pathFor(document), document);
      await this.atomicWrite(
        path.join(worktree, "views", "rubric-set.md"),
        renderMarkdown(nextManifest, [...existingDocuments.values()]),
      );
      const provenancePath = path.join(worktree, ".memory", "provenance.jsonl");
      await fs.mkdir(path.dirname(provenancePath), { recursive: true, mode: 0o700 });
      await fs.appendFile(provenancePath, `${JSON.stringify({
        runId,
        baseHead,
        changedPaths,
        sourceL1Ids: [...new Set(documents.flatMap((document) => document.rubrics.flatMap((item) => item.sourceL1Ids)))],
        committedAt: new Date().toISOString(),
      })}\n`, { mode: 0o600 });
      await this.git(["add", "--all"], worktree);
      await this.git(["commit", "-m", `memory: update L2B rubrics ${safeRunId}`], worktree);
      const resultHead = (await this.git(["rev-parse", "HEAD"], worktree)).trim();
      if (await this.head() !== baseHead) throw new Error("rubric_repository_head_changed");
      await this.git(["merge", "--ff-only", resultHead], this.root);
      return { head: resultHead, changedPaths, rubricSetVersion: nextManifest.version };
    } finally {
      await this.git(["worktree", "remove", "--force", worktree], this.root).catch(() => undefined);
      await fs.rm(worktree, { recursive: true, force: true });
    }
  }

  private validateScope(value: Pick<RubricPatch, "scope" | "scopeValue">): void {
    if (value.scope === "core" && value.scopeValue) throw new Error("core_scope_must_not_have_value");
    if (value.scope !== "core" && !value.scopeValue?.trim()) throw new Error("scope_value_required");
  }

  private validateDocument(document: StoredRubricDocument): void {
    this.validateScope(document);
    const ids = new Set<string>();
    const criterionKeys = new Set<string>();
    for (const item of document.rubrics) {
      if (!item.id.trim() || ids.has(item.id)) throw new Error(`invalid_or_duplicate_rubric_id:${item.id}`);
      ids.add(item.id);
      if (!item.criterionKey.trim() || criterionKeys.has(item.criterionKey)) throw new Error(`invalid_or_duplicate_criterion_key:${item.criterionKey}`);
      criterionKeys.add(item.criterionKey);
      if (!RUBRIC_OPERATIONS.includes(item.operation)) throw new Error(`invalid_rubric_operation:${item.id}`);
      if (!RUBRIC_DIMENSIONS.includes(item.dimension)) throw new Error(`invalid_rubric_dimension:${item.id}`);
      if (!item.label.trim() || !item.desc.trim() || !item.effect.trim()) throw new Error(`invalid_rubric_item:${item.id}`);
      if (item.redline !== false || item.status !== "active") throw new Error(`memory_rubric_must_be_non_redline_active:${item.id}`);
      if (item.sourceL1Ids.length === 0) throw new Error(`rubric_sources_required:${item.id}`);
      const requirementKeys = new Set<string>();
      for (const requirement of item.requirements ?? []) {
        if (!requirement.key.trim() || !requirement.text.trim() || requirementKeys.has(requirement.key)) {
          throw new Error(`invalid_or_duplicate_requirement:${item.id}:${requirement.key}`);
        }
        requirementKeys.add(requirement.key);
      }
      if (item.operation === "extend" && requirementKeys.size === 0) throw new Error(`extend_requirements_required:${item.id}`);
    }
  }

  private pathFor(value: Pick<RubricPatch, "scope" | "scopeValue">): string {
    if (value.scope === "core") return "system/rubrics.json";
    const root = value.scope === "audience" ? "audiences" : "projects";
    const scopeValue = this.normalizeScopeValue(value.scope, value.scopeValue ?? "");
    return `${root}/${storageSlug(scopeValue)}/rubrics.json`;
  }

  private normalizeScopeValue(scope: WritingMemoryScope, value: string): string {
    return scope === "audience" ? normalizeAudience(value) ?? value.trim() : value.trim();
  }

  private async readDocument(relativePath: string): Promise<LoadedRubricDocument | undefined> {
    let raw: string;
    try { raw = await this.git(["show", `HEAD:${relativePath}`], this.root); }
    catch { return undefined; }
    const parsed = JSON.parse(raw) as StoredRubricDocument & { schemaVersion: number };
    if (![1, 2].includes(parsed.schemaVersion)) throw new Error(`invalid_rubric_document_schema:${relativePath}`);
    const normalized: StoredRubricDocument = {
      ...parsed,
      schemaVersion: 2,
      rubrics: (parsed.rubrics ?? []).map((item) => ({
        ...item,
        criterionKey: item.criterionKey?.trim() || item.id,
        operation: item.operation ?? "add",
      })),
    };
    this.validateDocument(normalized);
    return { ...normalized, path: relativePath };
  }

  private async ensureManifest(): Promise<void> {
    try {
      await this.git(["show", "HEAD:manifest.json"], this.root);
      return;
    } catch { /* migrate legacy repository below */ }
    const manifest: RubricSetManifest = {
      schemaVersion: 1,
      product: "research_insight",
      version: "v0",
      versionNumber: 0,
      parentVersion: null,
      baseRubricVersion: "v2.3",
      updatedAt: new Date().toISOString(),
    };
    await this.atomicWrite(path.join(this.root, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
    const documents = (await Promise.all((await this.rubricPaths(this.root)).map((value) => this.readDocument(value))))
      .filter((value): value is LoadedRubricDocument => Boolean(value));
    await this.atomicWrite(path.join(this.root, "views", "rubric-set.md"), renderMarkdown(manifest, documents));
    await this.git(["add", "manifest.json", "views/rubric-set.md"], this.root);
    await this.git(["commit", "-m", "memory: initialize rubric set manifest"], this.root);
  }

  private async readManifest(revision: string): Promise<RubricSetManifest> {
    const raw = await this.git(["show", `${revision}:manifest.json`], this.root);
    const manifest = JSON.parse(raw) as RubricSetManifest;
    if (manifest.schemaVersion !== 1 || manifest.product !== "research_insight" || !Number.isInteger(manifest.versionNumber)) {
      throw new Error("invalid_rubric_set_manifest");
    }
    return manifest;
  }

  private async rubricPaths(root: string): Promise<string[]> {
    const results: string[] = [];
    const visit = async (directory: string) => {
      let entries: Array<import("node:fs").Dirent>;
      try { entries = await fs.readdir(directory, { withFileTypes: true }); }
      catch (error) {
        if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
        throw error;
      }
      for (const entry of entries) {
        if (entry.name === ".git" || entry.name === ".memory") continue;
        const full = path.join(directory, entry.name);
        if (entry.isDirectory()) await visit(full);
        else if (entry.isFile() && entry.name === "rubrics.json") results.push(path.relative(root, full));
      }
    };
    await visit(root);
    return results.sort();
  }

  private async atomicWrite(file: string, content: string): Promise<void> {
    await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
    const temporary = `${file}.${process.pid}.${crypto.randomBytes(4).toString("hex")}.tmp`;
    await fs.writeFile(temporary, content, { mode: 0o600 });
    await fs.rename(temporary, file);
  }

  private async git(args: string[], cwd: string): Promise<string> {
    const result = await execFileAsync("git", args, { cwd, encoding: "utf8", maxBuffer: 10 * 1024 * 1024 });
    return result.stdout;
  }
}
