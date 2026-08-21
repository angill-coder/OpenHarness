import crypto from "node:crypto";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { normalizeAudience, storageSlug } from "./scope-paths.ts";
import type { WritingMemoryScope } from "./runtime.ts";
import { L2_L3_MEMORY_DIR } from "./storage-layout.ts";

const execFileAsync = promisify(execFile);

export interface ContextItem {
  id: string;
  summary: string;
  rules: string[];
  sourceL1Ids: string[];
}

export interface RubricItem {
  id: string;
  criterion: string;
  pass: string;
  fail: string;
  status: "candidate" | "active";
  sourceL1Ids: string[];
}

export interface ContextDocumentChange {
  layer: "L2";
  scope: WritingMemoryScope;
  scopeValue?: string;
  title: string;
  description: string;
  items: ContextItem[];
}

export interface RubricDocumentChange {
  layer: "L3";
  scope: WritingMemoryScope;
  scopeValue?: string;
  title: string;
  description: string;
  items: RubricItem[];
}

export type MemoryDocumentChange = ContextDocumentChange | RubricDocumentChange;

export interface MemoryDocumentPatch {
  layer: "L2" | "L3";
  scope: WritingMemoryScope;
  scopeValue?: string;
  upsertItems?: Array<ContextItem | RubricItem>;
  removeItemIds?: string[];
}

export interface LoadedDocument {
  path: string;
  layer: "L2" | "L3";
  scope: WritingMemoryScope;
  scopeValue?: string;
  title: string;
  description: string;
  items: Array<ContextItem | RubricItem>;
}

function yamlString(value: string): string {
  return JSON.stringify(value);
}

function sourceComment(sourceL1Ids: string[]): string {
  return `<!-- sources: ${sourceL1Ids.join(", ")} -->`;
}

function serialize(change: MemoryDocumentChange): string {
  const lines = [
    "---",
    `description: ${yamlString(change.description.trim())}`,
    "---",
    "",
    `# ${change.title.trim()}`,
    "",
  ];
  for (const item of change.items) {
    lines.push(`## ${item.id}`, sourceComment(item.sourceL1Ids), "");
    if (change.layer === "L2") {
      const context = item as ContextItem;
      lines.push(context.summary.trim(), "", "### Rules", ...context.rules.map((rule) => `- ${rule.trim()}`), "");
    } else {
      const rubric = item as RubricItem;
      lines.push(
        `- **Criterion:** ${rubric.criterion.trim()}`,
        `- **Pass:** ${rubric.pass.trim()}`,
        `- **Fail:** ${rubric.fail.trim()}`,
        `- **Status:** ${rubric.status}`,
        "",
      );
    }
  }
  return `${lines.join("\n").trim()}\n`;
}

function parseDescription(content: string): string {
  const match = content.match(/^---\s*\n[\s\S]*?^description:\s*(.+?)\s*$[\s\S]*?^---\s*$/mu);
  if (!match) return "";
  try { return JSON.parse(match[1]) as string; }
  catch { return match[1].replace(/^['"]|['"]$/gu, ""); }
}

function parseTitle(content: string): string {
  return content.match(/^#\s+(.+)$/mu)?.[1]?.trim() ?? "";
}

function parseSources(content: string): string[] {
  const value = content.match(/<!--\s*sources:\s*([^\n]*?)\s*-->/iu)?.[1] ?? "";
  return [...new Set(value.split(/[,，\s]+/u).map((item) => item.trim()).filter(Boolean))];
}

function parseVisibleItems(content: string, layer: "L2" | "L3"): Array<ContextItem | RubricItem> {
  const headings = [...content.matchAll(/^##\s+(.+?)\s*$/gmu)];
  const items: Array<ContextItem | RubricItem> = [];
  for (let index = 0; index < headings.length; index += 1) {
    const heading = headings[index];
    const id = heading[1].trim();
    const start = (heading.index ?? 0) + heading[0].length;
    const end = headings[index + 1]?.index ?? content.length;
    const section = content.slice(start, end).trim();
    const sourceL1Ids = parseSources(section);
    if (!id || sourceL1Ids.length === 0) continue;
    if (layer === "L2") {
      const rulesHeading = section.match(/^###\s+Rules\s*$/imu);
      if (!rulesHeading || rulesHeading.index === undefined) continue;
      const summary = section
        .slice(0, rulesHeading.index)
        .replace(/<!--[^]*?-->/gu, "")
        .trim();
      const rules = section
        .slice(rulesHeading.index + rulesHeading[0].length)
        .split("\n")
        .map((line) => line.match(/^\s*[-*]\s+(.+?)\s*$/u)?.[1]?.trim())
        .filter((rule): rule is string => Boolean(rule));
      if (summary && rules.length > 0) items.push({ id, summary, rules, sourceL1Ids });
      continue;
    }
    const field = (name: string) => section.match(new RegExp(`^\\s*[-*]\\s+\\*\\*${name}:\\*\\*\\s*(.+?)\\s*$`, "imu"))?.[1]?.trim() ?? "";
    const criterion = field("Criterion");
    const pass = field("Pass");
    const fail = field("Fail");
    const rawStatus = field("Status");
    const status = rawStatus === "candidate" ? "candidate" : rawStatus === "active" ? "active" : undefined;
    if (criterion && pass && fail && status) items.push({ id, criterion, pass, fail, status, sourceL1Ids });
  }
  return items;
}

function parseLegacyItems(content: string, layer: "L2" | "L3"): Array<ContextItem | RubricItem> {
  const visibleCanonical = content.replace(/<!-- memory-item (\{[^\n]+\}) -->/gu, (_comment, raw: string) => {
    try {
      const item = JSON.parse(raw) as ContextItem | RubricItem;
      return sourceComment(Array.isArray(item.sourceL1Ids) ? item.sourceL1Ids : []);
    } catch {
      return "<!-- sources: -->";
    }
  });
  return parseVisibleItems(visibleCanonical, layer);
}

export class ContextRepository {
  readonly root: string;
  private readonly worktreesRoot: string;
  private writeQueue: Promise<unknown> = Promise.resolve();

  constructor(dataDir: string) {
    this.root = path.join(dataDir, L2_L3_MEMORY_DIR, "personal", "default");
    this.worktreesRoot = path.join(dataDir, L2_L3_MEMORY_DIR, "worktrees");
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
      await this.migrateLegacyDocuments();
      return;
    }

    await this.git(["init", "-b", "main"], this.root);
    await this.git(["config", "user.name", "Research Report Memory"], this.root);
    await this.git(["config", "user.email", "memory@local.invalid"], this.root);
    await fs.mkdir(path.join(this.root, "system"), { recursive: true, mode: 0o700 });
    await fs.mkdir(path.join(this.root, ".memory"), { recursive: true, mode: 0o700 });
    const initial: MemoryDocumentChange[] = [
      {
        layer: "L2", scope: "core", title: "Writing Core",
        description: "跨项目生效的长期报告写作要求。", items: [],
      },
      {
        layer: "L3", scope: "core", title: "Core Rubrics",
        description: "跨项目生效的报告写作自检与 Judge 标准。", items: [],
      },
    ];
    for (const document of initial) {
      await this.atomicWrite(path.join(this.root, this.pathFor(document)), serialize(document));
    }
    await this.atomicWrite(path.join(this.root, ".memory", "provenance.jsonl"), "");
    await this.git(["add", "--all"], this.root);
    await this.git(["commit", "-m", "memory: initialize context repository"], this.root);
  }

  async head(): Promise<string> {
    await this.initialize();
    return (await this.git(["rev-parse", "HEAD"], this.root)).trim();
  }

  async recall(input: { audience?: string; project?: string }): Promise<LoadedDocument[]> {
    await this.initialize();
    const candidates = [...new Set([
      "system/l2-context.md",
      "system/l3-rubrics.md",
      ...(input.audience?.trim()
        ? [`audiences/${storageSlug(input.audience)}/l2-context.md`, `audiences/${storageSlug(input.audience)}/l3-rubrics.md`]
        : []),
      ...(input.project?.trim()
        ? [`projects/${storageSlug(input.project)}/l2-context.md`, `projects/${storageSlug(input.project)}/l3-rubrics.md`]
        : []),
    ])];
    const documents: LoadedDocument[] = [];
    for (const relativePath of candidates) {
      const loaded = await this.readDocument(relativePath);
      if (loaded) documents.push(loaded);
    }
    return documents;
  }

  async snapshot(): Promise<{ head: string; documents: LoadedDocument[] }> {
    await this.initialize();
    const paths = await this.markdownPaths(this.root);
    const documents = (await Promise.all(paths.map((value) => this.readDocument(value))))
      .filter((value): value is LoadedDocument => Boolean(value));
    return { head: await this.head(), documents };
  }

  async applyDocuments(changes: MemoryDocumentChange[], runId: string): Promise<{ head: string; changedPaths: string[] }> {
    const operation = this.writeQueue.then(() => this.applyDocumentsUnlocked(changes, runId));
    this.writeQueue = operation.catch(() => undefined);
    return operation;
  }

  async applyDocumentPatches(patches: MemoryDocumentPatch[], runId: string): Promise<{ head: string; changedPaths: string[] }> {
    if (patches.length === 0) return { head: await this.head(), changedPaths: [] };
    const changes: MemoryDocumentChange[] = [];
    for (const patch of patches) {
      const relativePath = this.pathFor(patch);
      const current = await this.readDocument(relativePath);
      const removeIds = new Set(patch.removeItemIds ?? []);
      const upserts = new Map((patch.upsertItems ?? []).map((item) => [item.id, item]));
      const items = (current?.items ?? [])
        .filter((item) => !removeIds.has(item.id) && !upserts.has(item.id));
      items.push(...upserts.values());
      const scopeLabel = patch.scope === "core" ? "Core" : patch.scopeValue?.trim() || patch.scope;
      changes.push({
        layer: patch.layer,
        scope: patch.scope,
        ...(patch.scopeValue ? { scopeValue: patch.scopeValue } : {}),
        title: current?.title || (patch.layer === "L2" ? `${scopeLabel} Context` : `${scopeLabel} Rubrics`),
        description: current?.description || (patch.layer === "L2" ? "报告写作场景记忆。" : "报告写作自检与 Judge 标准。"),
        items,
      } as MemoryDocumentChange);
    }
    return this.applyDocuments(changes, runId);
  }

  async forgetItem(id: string, runId: string): Promise<{ deleted: number; head: string; changedPaths: string[] }> {
    const snapshot = await this.snapshot();
    const changes: MemoryDocumentChange[] = [];
    let deleted = 0;
    for (const document of snapshot.documents) {
      const items = document.items.filter((item) => {
        const remove = item.id === id;
        if (remove) deleted += 1;
        return !remove;
      });
      if (items.length === document.items.length) continue;
      changes.push({
        layer: document.layer,
        scope: document.scope,
        ...(document.scopeValue ? { scopeValue: document.scopeValue } : {}),
        title: document.title,
        description: document.description,
        items,
      } as MemoryDocumentChange);
    }
    if (changes.length === 0) return { deleted: 0, head: snapshot.head, changedPaths: [] };
    const result = await this.applyDocuments(changes, runId);
    return { deleted, ...result };
  }

  private async applyDocumentsUnlocked(changes: MemoryDocumentChange[], runId: string) {
    await this.initialize();
    if (changes.length === 0) return { head: await this.head(), changedPaths: [] };
    const baseHead = await this.head();
    const safeRunId = storageSlug(runId);
    const worktree = path.join(this.worktreesRoot, `${Date.now()}-${safeRunId}`);
    const changedPaths = [...new Set(changes.map((change) => this.pathFor(change)))];
    try {
      await this.git(["worktree", "add", "--detach", worktree, baseHead], this.root);
      for (const change of changes) {
        this.validate(change);
        await this.atomicWrite(path.join(worktree, this.pathFor(change)), serialize(change));
      }
      const documentStatus = await this.git(["status", "--porcelain", "--", ...changedPaths], worktree);
      if (!documentStatus.trim()) return { head: baseHead, changedPaths: [] };
      const provenancePath = path.join(worktree, ".memory", "provenance.jsonl");
      await fs.mkdir(path.dirname(provenancePath), { recursive: true, mode: 0o700 });
      await fs.appendFile(provenancePath, `${JSON.stringify({
        runId,
        baseHead,
        changedPaths,
        sourceL1Ids: [...new Set(changes.flatMap((change) => change.items.flatMap((item) => item.sourceL1Ids)))],
        committedAt: new Date().toISOString(),
      })}\n`, { mode: 0o600 });
      await this.git(["add", "--all"], worktree);
      await this.git(["commit", "-m", `memory: consolidate ${safeRunId}`], worktree);
      const resultHead = (await this.git(["rev-parse", "HEAD"], worktree)).trim();
      const currentHead = await this.head();
      if (currentHead !== baseHead) throw new Error("context_repository_head_changed");
      await this.git(["merge", "--ff-only", resultHead], this.root);
      return { head: resultHead, changedPaths };
    } finally {
      await this.git(["worktree", "remove", "--force", worktree], this.root).catch(() => undefined);
      await fs.rm(worktree, { recursive: true, force: true });
    }
  }

  private validate(change: MemoryDocumentChange): void {
    if (change.scope === "core" && change.scopeValue) throw new Error("core_scope_must_not_have_value");
    if (change.scope !== "core" && !change.scopeValue?.trim()) throw new Error("scope_value_required");
    if (!change.title.trim() || !change.description.trim()) throw new Error("document_title_and_description_required");
    const ids = new Set<string>();
    for (const item of change.items) {
      if (!item.id.trim() || ids.has(item.id)) throw new Error(`invalid_or_duplicate_item_id:${item.id}`);
      ids.add(item.id);
      if (item.sourceL1Ids.length === 0) throw new Error(`memory_item_sources_required:${item.id}`);
      if (change.layer === "L2" && (!(item as ContextItem).summary?.trim() || (item as ContextItem).rules.length === 0)) {
        throw new Error(`invalid_context_item:${item.id}`);
      }
      if (change.layer === "L3") {
        const rubric = item as RubricItem;
        if (!rubric.criterion?.trim() || !rubric.pass?.trim() || !rubric.fail?.trim()) {
          throw new Error(`invalid_rubric_item:${item.id}`);
        }
      }
    }
  }

  private pathFor(change: Pick<MemoryDocumentChange, "layer" | "scope" | "scopeValue">): string {
    const file = change.layer === "L2" ? "l2-context.md" : "l3-rubrics.md";
    if (change.scope === "core") return `system/${file}`;
    const root = change.scope === "audience" ? "audiences" : "projects";
    const scopeValue = change.scope === "audience"
      ? normalizeAudience(change.scopeValue) ?? ""
      : change.scopeValue ?? "";
    return `${root}/${storageSlug(scopeValue)}/${file}`;
  }

  private async readDocument(relativePath: string): Promise<LoadedDocument | undefined> {
    let content: string;
    try { content = await this.git(["show", `HEAD:${relativePath}`], this.root); }
    catch { return undefined; }
    const layer = this.layerForPath(relativePath);
    if (!layer) return undefined;
    const parts = relativePath.split("/");
    const scope: WritingMemoryScope = parts[0] === "audiences" ? "audience" : parts[0] === "projects" ? "project" : "core";
    return {
      path: relativePath,
      layer,
      scope,
      ...(scope !== "core" ? { scopeValue: parts[1] } : {}),
      title: parseTitle(content),
      description: parseDescription(content),
      items: parseVisibleItems(content, layer),
    };
  }

  private layerForPath(relativePath: string): "L2" | "L3" | undefined {
    const name = path.basename(relativePath);
    if (name === "l2-context.md" || name === "context.md" || name === "writing-core.md") return "L2";
    if (name === "l3-rubrics.md" || name === "rubrics.md" || name === "rubrics-core.md") return "L3";
    return undefined;
  }

  private async migrateLegacyDocuments(): Promise<void> {
    const paths = await this.markdownPaths(this.root);
    const legacy = paths.filter((relativePath) => {
      const name = path.basename(relativePath);
      return name === "writing-core.md" || name === "rubrics-core.md" || name === "context.md" || name === "rubrics.md";
    });
    if (legacy.length === 0) return;

    for (const oldPath of legacy) {
      const layer = this.layerForPath(oldPath);
      if (!layer) continue;
      const parts = oldPath.split("/");
      const newPath = parts[0] === "system"
        ? `system/${layer === "L2" ? "l2-context.md" : "l3-rubrics.md"}`
        : `${parts.slice(0, -1).join("/")}/${layer === "L2" ? "l2-context.md" : "l3-rubrics.md"}`;
      const target = path.join(this.root, newPath);
      try {
        await fs.access(target);
      } catch {
        const content = await fs.readFile(path.join(this.root, oldPath), "utf8");
        const items = parseLegacyItems(content, layer);
        const scope: WritingMemoryScope = parts[0] === "audiences" ? "audience" : parts[0] === "projects" ? "project" : "core";
        const change = {
          layer,
          scope,
          ...(scope !== "core" ? { scopeValue: parts[1] } : {}),
          title: parseTitle(content),
          description: parseDescription(content),
          items,
        } as MemoryDocumentChange;
        this.validate(change);
        await this.atomicWrite(target, serialize(change));
      }
      await fs.rm(path.join(this.root, oldPath));
    }
    await this.git(["add", "--all"], this.root);
    const status = await this.git(["status", "--porcelain"], this.root);
    if (status.trim()) await this.git(["commit", "-m", "memory: migrate Markdown schema and filenames"], this.root);
  }

  private async markdownPaths(root: string): Promise<string[]> {
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
        else if (entry.isFile() && entry.name.endsWith(".md")) results.push(path.relative(root, full));
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
