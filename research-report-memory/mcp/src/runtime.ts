import crypto from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { TdaiCore } from "../../node_modules/@tencentdb-agent-memory/memory-tencentdb/src/core/index.ts";
import type { HostAdapter, LLMRunnerFactory, Logger, RuntimeContext } from "../../node_modules/@tencentdb-agent-memory/memory-tencentdb/src/core/types.ts";
import { parseConfig } from "../../node_modules/@tencentdb-agent-memory/memory-tencentdb/src/config.ts";
import { generateMemoryId, writeMemory } from "../../node_modules/@tencentdb-agent-memory/memory-tencentdb/src/core/record/l1-writer.ts";
import { normalizeAudience } from "./scope-paths.ts";
import { ContextRepository, type ContextItem, type MemoryDocumentChange, type MemoryDocumentPatch, type RubricItem } from "./context-repository.ts";
import { classifyWritingFeedback } from "./relevance.ts";
import { L0_EPISODES_DIR, L1_ATOMS_DIR, MEMORYCORE_DIR, migrateStorageLayout } from "./storage-layout.ts";
import { ensureUserShortcut, removeManagedShortcut, resolveLegacyDocumentsShortcut, resolveUserShortcut } from "./user-shortcut.ts";

const SESSION_KEY = "research-report-memory-v2";
const EPISODE_RETENTION_DAYS = 14;

export const WRITING_MEMORY_SCOPES = ["core", "audience", "project"] as const;
export type WritingMemoryScope = (typeof WRITING_MEMORY_SCOPES)[number];

const logger: Logger = {
  debug: (message) => process.stderr.write(`${message}\n`),
  info: (message) => process.stderr.write(`${message}\n`),
  warn: (message) => process.stderr.write(`${message}\n`),
  error: (message) => process.stderr.write(`${message}\n`),
};

function resolveDataDir(): string {
  const configured = process.env.RESEARCH_REPORT_MEMORY_V2_DIR?.trim()
    || process.env.RESEARCH_REPORT_MEMORY_DIR?.trim();
  if (configured) return configured.replace(/^~(?=$|\/)/u, os.homedir());
  return path.join(os.homedir(), ".research-report-memory-v2-mvp");
}

export interface RecallInput {
  task: string;
  audience?: string;
  project?: string;
  limit?: number;
  includeL1?: boolean;
  purpose?: "writing" | "judge" | "review" | "maintenance";
}

export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
  messageId?: string;
  createdAt?: string;
}

export interface EpisodeInput {
  task: string;
  externalSourceId?: string;
  sessionId?: string;
  topic?: string;
  audience?: string;
  project?: string;
  stage?: string;
  contextBefore?: string;
  contextAfter?: string;
  reportBefore?: string;
  reportAfter?: string;
  judgeResult?: string;
  userEdit?: string;
  finalArtifact?: string;
  skillVersion?: string;
  rubricsVersion?: string;
  recalledMemoryIds?: string[];
  conversationExcerpt?: ConversationMessage[];
  conversationSource?: "host_context" | "workbuddy_trace_backfill" | "manual_backfill";
  conversationTruncated?: boolean;
  conversationOmissionReason?: string;
  recoveryActor?: "host_agent";
  recoveryReason?: string;
  proposedRule?: string;
}

export interface MemoryCandidate {
  operationRef?: string;
  rule: string;
  scope: WritingMemoryScope;
  scopeValue?: string;
  sourceEpisodeIds?: string[];
  expiresAt?: string;
  action?: "store" | "update" | "merge" | "skip";
  targetIds?: string[];
  lifecycle?: "candidate" | "active";
}

type DocumentItemInput = (Omit<ContextItem, "sourceL1Ids"> | Omit<RubricItem, "sourceL1Ids">) & {
  sourceL1Ids?: string[];
  sourceRefs?: string[];
};

export interface DocumentCandidate {
  layer: "L2" | "L3";
  scope: WritingMemoryScope;
  scopeValue?: string;
  title: string;
  description: string;
  items: DocumentItemInput[];
}

export interface DocumentPatchCandidate {
  layer: "L2" | "L3";
  scope: WritingMemoryScope;
  scopeValue?: string;
  upsertItems?: DocumentItemInput[];
  removeItemIds?: string[];
}

export interface CaptureInput {
  feedback: string;
  decision: "store" | "pending" | "ignore";
  mode?: "feedback" | "maintenance" | "manage";
  episode?: EpisodeInput;
  memories?: MemoryCandidate[];
  documents?: DocumentCandidate[];
  documentPatches?: DocumentPatchCandidate[];
  snapshotRevision?: string;
}

export interface RecoveryInput {
  recoveryId: string;
  failureReason: string;
  feedback: string;
  task: string;
  audience?: string;
  project?: string;
  stage?: string;
  contextBefore?: string;
  contextAfter?: string;
  reportBefore?: string;
  reportAfter?: string;
  proposedProjectRule?: string;
}

interface WritingMetadata {
  domain: "report_writing";
  scope: WritingMemoryScope;
  scopeValue?: string;
  sourceEpisodeIds: string[];
  expiresAt?: string;
  extractor: "wb_memory_subagent" | "host_recovery";
  lifecycle?: "candidate" | "active";
  schemaVersion: 3 | 4 | 5;
}

interface WritingEpisode extends EpisodeInput {
  id: string;
  episodeSchemaVersion: 2;
  feedback: string;
  status: "pending" | "recovery_pending" | "promoted" | "dismissed";
  promotedL1Ids: string[];
  candidateL1Ids: string[];
  createdAt: string;
  updatedAt: string;
}

interface DirtyTarget {
  scope: WritingMemoryScope;
  scopeValue?: string;
  memoryIds?: string[];
}

interface MaintenanceState {
  lastSuccessAt: string | null;
  nextDueAt: string | null;
  checkpoint: string | null;
  repositoryHead: string | null;
  dirtyTargets: DirtyTarget[];
  lastResult?: Record<string, unknown>;
}

const disabledRunnerFactory: LLMRunnerFactory = {
  createRunner: () => ({
    run: async () => { throw new Error("LLM extraction is disabled; WB Memory Sub-agent writes structured memory directly"); },
  }),
};

export class WritingMemoryRuntime {
  private readonly core: TdaiCore;
  private readonly dataDir: string;
  private readonly memoryCoreDir: string;
  private readonly l1AtomsDir: string;
  private readonly repository: ContextRepository;
  private initialized = false;

  constructor(_server: McpServer, dataDir = resolveDataDir()) {
    this.dataDir = dataDir;
    this.memoryCoreDir = path.join(dataDir, MEMORYCORE_DIR);
    this.l1AtomsDir = path.join(dataDir, L1_ATOMS_DIR);
    this.repository = new ContextRepository(dataDir);
    const runtimeContext: RuntimeContext = {
      userId: "default_user",
      sessionId: SESSION_KEY,
      sessionKey: SESSION_KEY,
      platform: "workbuddy",
      agentIdentity: "research-report-memory-v2-mvp",
      agentContext: "subagent",
      workspaceDir: process.cwd(),
      dataDir: this.memoryCoreDir,
    };
    const hostAdapter: HostAdapter = {
      hostType: "standalone",
      getRuntimeContext: () => runtimeContext,
      getLogger: () => logger,
      getLLMRunnerFactory: () => disabledRunnerFactory,
    };
    const config = parseConfig({
      capture: { enabled: true, l0l1RetentionDays: 0 },
      extraction: { enabled: false, enableDedup: false, maxMemoriesPerSession: 100 },
      pipeline: {
        everyNConversations: 1,
        enableWarmup: false,
        l1IdleTimeoutSeconds: 86400,
        l2DelayAfterL1Seconds: 86400,
        l2MinIntervalSeconds: 86400,
        l2MaxIntervalSeconds: 86400,
      },
      persona: { triggerEveryN: 100000 },
      recall: {
        enabled: true,
        maxResults: 30,
        maxCharsPerMemory: 800,
        maxTotalRecallChars: 10000,
        scoreThreshold: 0.15,
        strategy: "keyword",
        timeoutMs: 5000,
      },
      embedding: { enabled: false, provider: "none" },
      storeBackend: "sqlite",
      bm25: { enabled: false, language: "zh" },
      llm: { enabled: false },
      report: { enabled: false },
    });
    this.core = new TdaiCore({ hostAdapter, config });
  }

  async initialize(): Promise<void> {
    if (this.initialized) return;
    await migrateStorageLayout(this.dataDir, {
      allowLegacyMigration: process.env.RESEARCH_REPORT_MEMORY_ALLOW_STORAGE_MIGRATION !== "0",
    });
    await this.core.initialize();
    await this.core.handleBeforeRecall("报告写作记忆初始化", SESSION_KEY);
    await this.purgeLegacyL0Mirror();
    await fs.mkdir(this.episodesDir(), { recursive: true, mode: 0o700 });
    await fs.mkdir(path.dirname(this.maintenanceStatePath()), { recursive: true, mode: 0o700 });
    await this.repository.initialize();
    const legacyShortcut = resolveLegacyDocumentsShortcut(this.dataDir);
    if (legacyShortcut) {
      try { await removeManagedShortcut(this.repository.root, legacyShortcut); }
      catch (error) { logger.warn(`Legacy Documents shortcut could not be removed: ${error instanceof Error ? error.message : String(error)}`); }
    }
    const shortcut = resolveUserShortcut(this.dataDir);
    if (shortcut) {
      try {
        const result = await ensureUserShortcut(this.repository.root, shortcut);
        if (result === "skipped") logger.warn(`User shortcut already exists and was not replaced: ${shortcut}`);
      } catch (error) {
        logger.warn(`User shortcut could not be created: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    this.initialized = true;
  }

  async recall(input: RecallInput) {
    await this.initialize();
    if (input.purpose === "maintenance") return this.maintenanceSnapshot(input.limit ?? 100);
    if (input.purpose === "review") return this.reviewSnapshot(input, input.limit ?? 100);

    const documents = await this.repository.recall({ audience: input.audience, project: input.project });
    const contextItems = this.highLevelItems<ContextItem>(documents, "L2");
    const rubricItems = this.highLevelItems<RubricItem>(documents, "L3").filter((value) => value.item.status === "active");
    const specificMemories = input.includeL1 ? await this.recallL1(input, input.limit ?? 12) : [];
    const recallPlan = {
      writing_context: {
        core: { items: contextItems.filter((value) => value.scope === "core").flatMap((value) => [value.item.summary, ...value.item.rules]) },
        audience: { name: input.audience ?? null, items: contextItems.filter((value) => value.scope === "audience").flatMap((value) => [value.item.summary, ...value.item.rules]) },
        project: { name: input.project ?? null, items: contextItems.filter((value) => value.scope === "project").flatMap((value) => [value.item.summary, ...value.item.rules]) },
      },
      self_checklist: {
        core: { items: rubricItems.filter((value) => value.scope === "core").map((value) => value.item.criterion) },
        audience: { name: input.audience ?? null, items: rubricItems.filter((value) => value.scope === "audience").map((value) => value.item.criterion) },
        project: { name: input.project ?? null, items: rubricItems.filter((value) => value.scope === "project").map((value) => value.item.criterion) },
      },
      specific_memories: specificMemories,
    };
    return {
      status: "ok",
      purpose: input.purpose ?? "writing",
      recallPlan,
      context: this.renderRecall(recallPlan),
      judgeRubrics: rubricItems.map((value) => ({ ...value.item, scope: value.scope, scopeValue: value.scopeValue })),
      sources: { l2: contextItems.map((value) => value.item.id), l3: rubricItems.map((value) => value.item.id), l1: specificMemories.map((value) => value.id) },
      repositoryHead: await this.repository.head(),
      instruction: "按 本轮明确要求 > project > audience > core > research-report skill 应用；L3 同时作为写作自检清单和 Judge Rubrics。",
    };
  }

  async capture(input: CaptureInput) {
    await this.initialize();
    if (input.decision === "ignore") return { status: "ignored", stored: false, reason: "memory_subagent_decision_ignore", records: [] };
    const maintenance = input.mode === "maintenance";
    const feedbackMode = !input.mode || input.mode === "feedback";
    if (feedbackMode && !input.snapshotRevision) {
      return { status: "error", stored: false, reason: "feedback_review_snapshot_required", records: [] };
    }
    if (input.snapshotRevision) {
      const snapshotRevision = await this.currentStorageRevision();
      if (snapshotRevision !== input.snapshotRevision) {
        return { status: "conflict", stored: false, reason: "memory_snapshot_changed", snapshotRevision, records: [] };
      }
    }

    const externalSourceId = input.episode?.externalSourceId?.trim()
      || (!maintenance && input.episode ? this.feedbackSourceId(input) : undefined);
    if (input.episode && externalSourceId && !input.episode.externalSourceId) {
      input = { ...input, episode: { ...input.episode, externalSourceId } };
    }
    const existingEpisode = !maintenance && externalSourceId
      ? (await this.readEpisodes()).find((item) => item.externalSourceId === externalSourceId)
      : undefined;
    const conversationError = feedbackMode ? this.validateConversationExcerpt(input) : undefined;
    if (existingEpisode?.status === "promoted" || (existingEpisode && input.decision === "pending")) {
      let l0ConversationEnriched = false;
      if (!existingEpisode.conversationExcerpt?.length && input.episode?.conversationExcerpt?.length) {
        if (conversationError) return { status: "error", stored: false, ...conversationError, records: [] };
        const compact = this.compactEpisode(input.episode);
        existingEpisode.conversationExcerpt = compact.conversationExcerpt;
        existingEpisode.conversationSource = compact.conversationSource;
        existingEpisode.conversationTruncated = compact.conversationTruncated;
        existingEpisode.conversationOmissionReason = compact.conversationOmissionReason;
        existingEpisode.episodeSchemaVersion = 2;
        existingEpisode.updatedAt = new Date().toISOString();
        await this.persistEpisode(existingEpisode);
        l0ConversationEnriched = true;
      }
      return {
        status: existingEpisode.status === "promoted" ? "unchanged" : "pending",
        stored: true,
        idempotent: true,
        episodeId: existingEpisode.id,
        l0ConversationEnriched,
        writtenIds: existingEpisode.promotedL1Ids,
        reviewedLayers: ["L0", "L1", "L2", "L3"],
        ...await this.activeMemoryFor(existingEpisode),
        records: [],
      };
    }

    const relevance = maintenance ? { relevant: true, reason: "maintenance", writingText: input.feedback } : classifyWritingFeedback(input.feedback);
    if (!relevance.relevant) return { status: "ignored", stored: false, reason: relevance.reason, records: [] };
    if (!maintenance && !input.episode?.task.trim()) return {
      status: "error", stored: false,
      reason: "episode_task_missing_in_payload",
      hint: "feedback capture requires episode: { task, ... }; Runtime creates the Episode in this call",
      records: [],
    };
    if (conversationError) return { status: "error", stored: false, ...conversationError, records: [] };
    const episode = maintenance ? undefined : existingEpisode ?? await this.saveEpisode({
      ...input.episode!, feedback: input.feedback.trim(), status: "pending",
    });
    if (input.decision === "pending") return {
      status: "pending",
      stored: true,
      layer: "L0",
      episodeId: episode!.id,
      reviewedLayers: ["L0", "L1", "L2", "L3"],
      ...await this.activeMemoryFor(episode!),
      records: [],
    };

    const requested = input.memories ?? [];
    const documentCandidates = input.documents ?? [];
    const documentPatchCandidates = input.documentPatches ?? [];
    if (requested.length === 0 && documentCandidates.length === 0 && documentPatchCandidates.length === 0) return { status: "error", stored: false, reason: "structured_memories_or_documents_required", records: [] };
    const store = this.core.getVectorStore();
    if (!store) return { status: "error", stored: false, reason: "memory_store_unavailable", records: [] };
    const existing = await store.queryL1Records({ sessionKey: SESSION_KEY });
    const records: Array<Record<string, unknown>> = [];
    const writtenIds: string[] = [];
    const operationIds = new Map<string, string>();
    const touchedEpisodes = new Set<string>();
    const dirtyTargets: DirtyTarget[] = [];
    const sessionId = `wb-memory-subagent-${Date.now()}`;

    try {
      this.validateCapturePlan(requested, documentCandidates, documentPatchCandidates, existing);
      for (const candidate of requested) {
        const write = await this.writeL1(candidate, episode, existing, sessionId);
        records.push(write.record);
        if (write.id) {
          writtenIds.push(write.id);
          if (candidate.operationRef) operationIds.set(candidate.operationRef, write.id);
          for (const sourceId of write.sourceEpisodeIds) touchedEpisodes.add(sourceId);
          dirtyTargets.push({
            scope: candidate.scope,
            ...(candidate.scopeValue ? { scopeValue: candidate.scopeValue.trim() } : {}),
            memoryIds: [...new Set([write.id, ...(candidate.targetIds ?? [])])],
          });
        }
      }
      const currentRows = await store.queryL1Records({ sessionKey: SESSION_KEY });
      const activeIds = new Set(currentRows
        .filter((row) => this.parseMetadata(row.metadata_json)?.lifecycle !== "candidate")
        .map((row) => row.record_id));
      const documents = this.resolveDocuments(documentCandidates, operationIds, activeIds);
      const documentPatches = this.resolveDocumentPatches(documentPatchCandidates, operationIds, activeIds);
      let repositoryResult = { head: await this.repository.head(), changedPaths: [] as string[] };
      if (documents.length > 0) repositoryResult = await this.repository.applyDocuments(documents, `${maintenance ? "maintenance" : "feedback-review"}-${Date.now()}`);
      if (documentPatches.length > 0) {
        const patchResult = await this.repository.applyDocumentPatches(documentPatches, `${maintenance ? "maintenance-patch" : "feedback-review-patch"}-${Date.now()}`);
        repositoryResult = {
          head: patchResult.head,
          changedPaths: [...new Set([...repositoryResult.changedPaths, ...patchResult.changedPaths])],
        };
      }
      const hasEffect = writtenIds.length > 0 || repositoryResult.changedPaths.length > 0;
      if (!hasEffect) {
        return {
          status: "error", stored: false, reason: "capture_plan_no_effect", retriable: true,
          episodeId: episode?.id, written: 0, writtenIds: [], documentsWritten: 0,
          changedPaths: [], repositoryHead: repositoryResult.head, records,
        };
      }
      await this.updateDirtyTargets(dirtyTargets, [...documents, ...documentPatches], repositoryResult.head);
      for (const episodeId of touchedEpisodes) await this.markEpisodePromoted(episodeId, writtenIds);
      if (episode) await this.markEpisodePromoted(episode.id, writtenIds);
      const activeMemory = input.episode ? await this.activeMemoryFor(input.episode) : { activeRubrics: [], memoryContext: "" };
      return {
        status: "stored",
        stored: true,
        episodeId: episode?.id,
        written: writtenIds.length,
        writtenIds,
        documentsWritten: repositoryResult.changedPaths.length,
        changedPaths: repositoryResult.changedPaths,
        repositoryHead: repositoryResult.head,
        reviewedLayers: ["L0", "L1", "L2", "L3"],
        activeRubrics: activeMemory.activeRubrics,
        memoryContext: activeMemory.memoryContext,
        dirtyTargets: (await this.readMaintenanceState()).dirtyTargets,
        records,
        sessionId,
      };
    } catch (error) {
      if (writtenIds.length > 0) await store.deleteL1Batch(writtenIds);
      await this.purgeJsonl(path.join(this.l1AtomsDir, "records"), (record) => typeof record.id === "string" && writtenIds.includes(record.id));
      const reason = error instanceof Error ? error.message : String(error);
      if (reason === "target_ids_required") {
        return {
          status: "error",
          stored: false,
          reason,
          hint: 'action=update|merge requires targetIds: ["<existing L1 ID>"]; id and targetId are invalid.',
          expectedMemoryShape: { action: "update", targetIds: ["m_existing_l1_id"] },
          records: [],
        };
      }
      return { status: "error", stored: false, reason, records: [] };
    }
  }

  async recover(input: RecoveryInput) {
    await this.initialize();
    const relevance = classifyWritingFeedback(input.feedback);
    if (!relevance.relevant) return { status: "ignored", stored: false, reason: relevance.reason, records: [] };

    const recoveryId = input.recoveryId.trim();
    const existingEpisode = (await this.readEpisodes()).find((item) => item.externalSourceId === recoveryId);
    if (existingEpisode) {
      return {
        status: "pending",
        stored: true,
        idempotent: true,
        layer: existingEpisode.candidateL1Ids?.length ? "L0+L1-candidate" : "L0",
        episodeId: existingEpisode.id,
        candidateL1Ids: existingEpisode.candidateL1Ids ?? [],
        reviewRequired: true,
        records: [],
      };
    }

    const episode = await this.saveEpisode({
      task: input.task,
      externalSourceId: recoveryId,
      audience: input.audience,
      project: input.project,
      stage: input.stage ?? "host-recovery",
      contextBefore: input.contextBefore,
      contextAfter: input.contextAfter,
      reportBefore: input.reportBefore,
      reportAfter: input.reportAfter,
      conversationExcerpt: [{ role: "user", content: input.feedback.trim() }],
      conversationSource: "host_context",
      conversationTruncated: true,
      conversationOmissionReason: "host recovery did not receive the preceding assistant conversation",
      recoveryActor: "host_agent",
      recoveryReason: input.failureReason,
      proposedRule: input.proposedProjectRule,
      feedback: input.feedback.trim(),
      status: "recovery_pending",
    });
    const candidateL1Ids: string[] = [];
    const records: Array<Record<string, unknown>> = [];
    const project = input.project?.trim();
    const proposedRule = input.proposedProjectRule?.trim();
    if (project && proposedRule) {
      const store = this.core.getVectorStore();
      if (!store) return { status: "error", stored: false, reason: "memory_store_unavailable", records: [] };
      const existing = await store.queryL1Records({ sessionKey: SESSION_KEY });
      const write = await this.writeL1({
        rule: proposedRule,
        scope: "project",
        scopeValue: project,
        lifecycle: "candidate",
      }, episode, existing, `host-recovery-${Date.now()}`, "host_recovery");
      if (write.id && write.lifecycle === "candidate") candidateL1Ids.push(write.id);
      records.push(write.record);
      await this.updateDirtyTargets([{ scope: "project", scopeValue: project }], [], await this.repository.head());
    }
    await this.markEpisodeRecoveryPending(episode.id, candidateL1Ids);
    return {
      status: "pending",
      stored: true,
      layer: candidateL1Ids.length ? "L0+L1-candidate" : "L0",
      episodeId: episode.id,
      candidateL1Ids,
      reviewRequired: true,
      restriction: "宿主 Recovery 只保存 L0 与 project L1 candidate；不得激活 audience/core 或改写 L2/L3。",
      records,
    };
  }

  async forget(input: { query?: string; id?: string; includeEpisodes?: boolean }) {
    await this.initialize();
    const query = input.query?.trim() ?? "";
    const id = input.id?.trim() ?? "";
    if (!query && !id) return { status: "ignored", deleted: 0, reason: "query_or_id_required" };
    const store = this.core.getVectorStore();
    if (!store) return { status: "error", deleted: 0, reason: "memory_store_unavailable" };
    const rows = await store.queryL1Records({ sessionKey: SESSION_KEY });
    const matches = rows.filter((row) => id ? row.record_id === id : this.textMatches(row.content, query));
    const ids = matches.map((row) => row.record_id);
    if (ids.length > 0) {
      await store.deleteL1Batch(ids);
      await this.purgeJsonl(path.join(this.l1AtomsDir, "records"), (record) => typeof record.id === "string" && ids.includes(record.id));
      await this.updateDirtyTargets(matches.flatMap((row) => {
        const metadata = this.parseMetadata(row.metadata_json);
        return metadata ? [{ scope: metadata.scope, scopeValue: metadata.scopeValue }] : [];
      }), [], await this.repository.head());
    }
    const highLevel = id ? await this.repository.forgetItem(id, `forget-${Date.now()}`) : { deleted: 0, head: await this.repository.head(), changedPaths: [] };
    let deletedEpisodes = 0;
    if (input.includeEpisodes) {
      for (const episode of (await this.readEpisodes()).filter((item) => id ? item.id === id : this.textMatches(JSON.stringify(item), query))) {
        await fs.rm(this.episodePath(episode.id), { force: true });
        deletedEpisodes += 1;
      }
    }
    return { status: "ok", deleted: ids.length + highLevel.deleted, deletedL1: ids.length, deletedHighLevel: highLevel.deleted, deletedEpisodes, deletedIds: ids, changedPaths: highLevel.changedPaths, repositoryHead: highLevel.head };
  }

  async destroy(): Promise<void> { await this.core.destroy(); }

  private async purgeLegacyL0Mirror(): Promise<void> {
    const store = this.core.getVectorStore();
    if (!store) throw new Error("memory_store_unavailable");
    for (let page = 0; page < 100; page += 1) {
      const rows = await store.queryL0ForL1(SESSION_KEY, undefined, 1000);
      if (rows.length === 0) return;
      let deleted = 0;
      for (const row of rows) {
        if (await store.deleteL0(row.record_id)) deleted += 1;
      }
      if (deleted === 0) throw new Error("legacy_l0_mirror_cleanup_failed");
    }
    throw new Error("legacy_l0_mirror_cleanup_exceeded_limit");
  }

  private async activeMemoryFor(input: Pick<EpisodeInput, "task" | "audience" | "project">) {
    const recalled = await this.recall({ task: input.task, audience: input.audience, project: input.project, purpose: "writing" });
    return {
      activeRubrics: "judgeRubrics" in recalled ? recalled.judgeRubrics : [],
      memoryContext: "context" in recalled ? recalled.context : "",
    };
  }

  private async recallL1(input: RecallInput, limit: number) {
    const store = this.core.getVectorStore();
    if (!store) return [];
    const now = Date.now();
    const applicable = (await store.queryL1Records({ sessionKey: SESSION_KEY }))
      .map((row) => ({ row, metadata: this.parseMetadata(row.metadata_json) }))
      .filter((value) => value.metadata?.domain === "report_writing")
      .filter((value) => value.metadata!.lifecycle !== "candidate")
      .filter((value) => !value.metadata!.expiresAt || Date.parse(value.metadata!.expiresAt) > now)
      .filter((value) => this.scopeApplies(value.metadata!, input));
    return applicable
      .sort((a, b) => this.scopeScore(b.metadata!) - this.scopeScore(a.metadata!) || b.row.updated_time.localeCompare(a.row.updated_time))
      .slice(0, limit)
      .map((value) => ({ id: value.row.record_id, rule: value.row.content, scope: value.metadata!.scope, ...(value.metadata!.scopeValue ? { scopeValue: value.metadata!.scopeValue } : {}) }));
  }

  private highLevelItems<T extends ContextItem | RubricItem>(documents: Awaited<ReturnType<ContextRepository["recall"]>>, layer: "L2" | "L3") {
    return documents.filter((document) => document.layer === layer)
      .flatMap((document) => document.items.map((item) => ({ item: item as T, scope: document.scope, scopeValue: document.scopeValue })));
  }

  private async writeL1(
    candidate: MemoryCandidate,
    episode: WritingEpisode | undefined,
    existing: any[],
    sessionId: string,
    extractor: WritingMetadata["extractor"] = "wb_memory_subagent",
  ) {
    const rule = candidate.rule.trim();
    const scopeValue = candidate.scope === "audience"
      ? normalizeAudience(candidate.scopeValue)
      : candidate.scopeValue?.trim();
    const action = candidate.action ?? "store";
    const targets = candidate.targetIds ?? [];
    this.validateCandidate(candidate, rule, scopeValue, existing);
    if (action === "skip") return { record: { status: "skipped", rule }, sourceEpisodeIds: [] as string[] };
    const duplicate = existing.find((row) => {
      const metadata = this.parseMetadata(row.metadata_json);
      return this.normalize(row.content) === this.normalize(rule) && metadata?.scope === candidate.scope
        && this.scopeValuesEqual(candidate.scope, metadata.scopeValue, scopeValue);
    });
    if (duplicate && action === "store") {
      const metadata = this.parseMetadata(duplicate.metadata_json);
      return {
        id: duplicate.record_id,
        lifecycle: metadata?.lifecycle ?? "active",
        record: { id: duplicate.record_id, status: "unchanged", rule },
        sourceEpisodeIds: [] as string[],
      };
    }
    const id = generateMemoryId();
    const sourceEpisodeIds = [...new Set([...(candidate.sourceEpisodeIds ?? []), ...(episode ? [episode.id] : [])])];
    const metadata: WritingMetadata = {
      domain: "report_writing", scope: candidate.scope,
      ...(scopeValue ? { scopeValue } : {}), sourceEpisodeIds,
      ...(candidate.expiresAt ? { expiresAt: candidate.expiresAt } : {}),
      extractor,
      lifecycle: candidate.lifecycle ?? "active",
      schemaVersion: 5,
    };
    const store = this.core.getVectorStore();
    if (!store) throw new Error("memory_store_unavailable");
    const result = await writeMemory({
      memory: {
        content: rule,
        type: "instruction",
        priority: (metadata.lifecycle === "candidate" ? 50 : 70) + this.scopeScore(metadata) * 5,
        source_message_ids: sourceEpisodeIds,
        metadata: metadata as never,
        scene_name: "报告写作记忆",
      },
      decision: { record_id: id, action, target_ids: targets, ...(action === "update" || action === "merge" ? { merged_content: rule } : {}) },
      baseDir: this.l1AtomsDir, sessionKey: SESSION_KEY, sessionId, logger, vectorStore: store,
    });
    const verified = (await store.queryL1Records({ sessionId })).some((row) => row.record_id === id);
    if (!result || !verified) throw new Error(`l1_write_verification_failed:${id}`);
    return { id, lifecycle: metadata.lifecycle, record: { id, status: action === "store" ? "stored" : action, rule, replacedIds: targets }, sourceEpisodeIds };
  }

  private validateCapturePlan(
    candidates: MemoryCandidate[],
    documents: DocumentCandidate[],
    documentPatches: DocumentPatchCandidate[],
    existing: any[],
  ): void {
    for (const candidate of candidates) {
      this.validateCandidate(candidate, candidate.rule.trim(), candidate.scopeValue?.trim(), existing);
    }
    const existingIds = new Set(existing.map((row) => row.record_id));
    const removedIds = new Set(candidates.flatMap((candidate) =>
      ["update", "merge"].includes(candidate.action ?? "store") ? candidate.targetIds ?? [] : []));
    const operationCandidates = new Map<string, MemoryCandidate>();
    for (const candidate of candidates.filter((value) => (value.action ?? "store") !== "skip")) {
      if (!candidate.operationRef) continue;
      if (operationCandidates.has(candidate.operationRef)) throw new Error(`operation_ref_duplicate:${candidate.operationRef}`);
      operationCandidates.set(candidate.operationRef, candidate);
    }
    const documentInputs = [
      ...documents.map((document) => ({ document, items: document.items })),
      ...documentPatches.map((document) => ({ document, items: document.upsertItems ?? [] })),
    ];
    for (const { document, items } of documentInputs) {
      for (const item of items) {
        const refs = item.sourceRefs ?? item.sourceL1Ids ?? [];
        if (refs.length === 0) throw new Error(`document_source_not_found:${item.id}`);
        for (const ref of refs) {
          if (ref.startsWith("new:")) {
            const candidate = operationCandidates.get(ref.slice(4));
            if (!candidate) throw new Error(`document_source_ref_not_found:${ref}`);
            if (!this.documentSourceScopeMatches(document, candidate.scope, candidate.scopeValue)) {
              throw new Error(`document_source_scope_mismatch:${item.id}:${ref}`);
            }
            continue;
          }
          const id = ref.startsWith("existing:") ? ref.slice(9) : ref;
          if (!existingIds.has(id) || removedIds.has(id)) throw new Error(`document_source_not_found:${item.id}`);
          const row = existing.find((value) => value.record_id === id);
          const metadata = row ? this.parseMetadata(row.metadata_json) : undefined;
          if (metadata?.lifecycle === "candidate") {
            throw new Error(`document_source_candidate_not_active:${item.id}`);
          }
          if (!metadata || !this.documentSourceScopeMatches(document, metadata.scope, metadata.scopeValue)) {
            throw new Error(`document_source_scope_mismatch:${item.id}:${id}`);
          }
        }
      }
    }
  }

  private documentSourceScopeMatches(document: Pick<DocumentCandidate, "scope" | "scopeValue">, sourceScope: WritingMemoryScope, sourceScopeValue?: string): boolean {
    return document.scope === sourceScope
      && this.scopeValuesEqual(sourceScope, document.scopeValue, sourceScopeValue);
  }

  private resolveDocuments(candidates: DocumentCandidate[], operationIds: Map<string, string>, validIds: Set<string>): MemoryDocumentChange[] {
    return candidates.map((document) => ({
      ...document,
      items: document.items.map((item) => {
        const refs = item.sourceRefs ?? item.sourceL1Ids ?? [];
        const sourceL1Ids = refs.map((value) => {
          if (value.startsWith("new:")) {
            const resolved = operationIds.get(value.slice(4));
            if (!resolved) throw new Error(`document_source_ref_not_found:${value}`);
            return resolved;
          }
          return value.startsWith("existing:") ? value.slice(9) : value;
        });
        if (sourceL1Ids.length === 0 || sourceL1Ids.some((value) => !validIds.has(value))) throw new Error(`document_source_not_found:${item.id}`);
        const result = { ...item, sourceL1Ids: [...new Set(sourceL1Ids)] } as Record<string, unknown>;
        delete result.sourceRefs;
        return result;
      }),
    })) as unknown as MemoryDocumentChange[];
  }

  private resolveDocumentPatches(candidates: DocumentPatchCandidate[], operationIds: Map<string, string>, validIds: Set<string>): MemoryDocumentPatch[] {
    return candidates.map((document) => ({
      layer: document.layer,
      scope: document.scope,
      ...(document.scopeValue ? { scopeValue: document.scopeValue } : {}),
      ...(document.removeItemIds?.length ? { removeItemIds: [...new Set(document.removeItemIds)] } : {}),
      upsertItems: (document.upsertItems ?? []).map((item) => {
        const refs = item.sourceRefs ?? item.sourceL1Ids ?? [];
        const sourceL1Ids = refs.map((value) => {
          if (value.startsWith("new:")) {
            const resolved = operationIds.get(value.slice(4));
            if (!resolved) throw new Error(`document_source_ref_not_found:${value}`);
            return resolved;
          }
          return value.startsWith("existing:") ? value.slice(9) : value;
        });
        if (sourceL1Ids.length === 0 || sourceL1Ids.some((value) => !validIds.has(value))) throw new Error(`document_source_not_found:${item.id}`);
        const result = { ...item, sourceL1Ids: [...new Set(sourceL1Ids)] } as Record<string, unknown>;
        delete result.sourceRefs;
        return result as unknown as ContextItem | RubricItem;
      }),
    }));
  }

  private async maintenanceSnapshot(limit: number) {
    const expiredEpisodes = await this.cleanupExpiredPendingEpisodes();
    const store = this.core.getVectorStore();
    if (!store) return { status: "error", reason: "memory_store_unavailable" };
    const rows = await store.queryL1Records({ sessionKey: SESSION_KEY });
    const pendingEpisodes = (await this.readEpisodes())
      .filter((episode) => episode.status === "pending" || episode.status === "recovery_pending")
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt))
      .slice(0, limit);
    const repository = await this.repository.snapshot();
    const state = await this.readMaintenanceState();
    const memories = rows
      .map((row) => ({ row, metadata: this.parseMetadata(row.metadata_json) }))
      .filter((value) => value.metadata?.domain === "report_writing");
    const pendingEpisodeIds = new Set(pendingEpisodes.map((episode) => episode.id));
    const selectedIds = new Set<string>();
    const conflictGroups: Array<Record<string, unknown>> = [];
    const targetMatches = (target: DirtyTarget, metadata: WritingMetadata) =>
      target.scope === metadata.scope && this.scopeValuesEqual(target.scope, target.scopeValue, metadata.scopeValue);

    for (const value of memories) {
      if (value.metadata!.sourceEpisodeIds.some((id) => pendingEpisodeIds.has(id))) selectedIds.add(value.row.record_id);
      for (const target of state.dirtyTargets) {
        if (!targetMatches(target, value.metadata!)) continue;
        if (target.memoryIds?.includes(value.row.record_id)) selectedIds.add(value.row.record_id);
      }
    }
    for (const target of state.dirtyTargets.filter((value) => !value.memoryIds?.length)) {
      memories
        .filter((value) => targetMatches(target, value.metadata!))
        .sort((a, b) => b.row.updated_time.localeCompare(a.row.updated_time))
        .slice(0, 12)
        .forEach((value) => selectedIds.add(value.row.record_id));
    }

    const byScope = new Map<string, typeof memories>();
    for (const value of memories) {
      const key = `${value.metadata!.scope}:${value.metadata!.scopeValue ?? ""}`.toLocaleLowerCase("zh-CN");
      byScope.set(key, [...(byScope.get(key) ?? []), value]);
    }
    for (const values of byScope.values()) {
      const exactGroups = new Map<string, typeof memories>();
      for (const value of values) {
        const key = this.normalize(value.row.content);
        exactGroups.set(key, [...(exactGroups.get(key) ?? []), value]);
      }
      for (const group of exactGroups.values()) {
        if (group.length < 2) continue;
        const ids = group.map((value) => value.row.record_id);
        ids.forEach((id) => selectedIds.add(id));
        conflictGroups.push({ reason: "exact_duplicate", memoryIds: ids });
      }

      const seeds = values.filter((value) => selectedIds.has(value.row.record_id));
      for (const seed of seeds) {
        const related = values
          .filter((value) => value.row.record_id !== seed.row.record_id && this.rulesPossiblyOverlap(seed.row.content, value.row.content))
          .slice(0, 6);
        if (related.length === 0) continue;
        const ids = [seed.row.record_id, ...related.map((value) => value.row.record_id)];
        ids.forEach((id) => selectedIds.add(id));
        conflictGroups.push({
          reason: "possible_overlap_or_conflict",
          scope: seed.metadata!.scope,
          ...(seed.metadata!.scopeValue ? { scopeValue: seed.metadata!.scopeValue } : {}),
          memoryIds: ids,
        });
      }
    }

    const l1Memories = memories
      .filter((value) => selectedIds.has(value.row.record_id))
      .sort((a, b) => b.row.updated_time.localeCompare(a.row.updated_time))
      .slice(0, Math.max(limit, 20))
      .map((value) => ({ id: value.row.record_id, rule: value.row.content, ...value.metadata }));
    const includedIds = new Set(l1Memories.map((memory) => memory.id));
    const contextDocuments = repository.documents.flatMap((document) => {
      const items = document.items.filter((item) => item.sourceL1Ids.some((id) => includedIds.has(id)));
      if (items.length === 0) return [];
      return [{ ...document, items }];
    });
    const noWork = pendingEpisodes.length === 0 && state.dirtyTargets.length === 0 && conflictGroups.length === 0;
    const payload = {
      status: "ok",
      purpose: "maintenance",
      noWork,
      pendingEpisodes,
      l1Memories,
      dirtyTargets: state.dirtyTargets,
      suspectedConflicts: conflictGroups,
      contextDocuments,
      repositoryHead: repository.head,
      expiredEpisodes,
      workSummary: {
        pendingEpisodes: pendingEpisodes.length,
        dirtyTargets: state.dirtyTargets.length,
        suspectedConflicts: conflictGroups.length,
        selectedL1: l1Memories.length,
        selectedDocumentItems: contextDocuments.reduce((sum, document) => sum + document.items.length, 0),
      },
      instruction: noWork
        ? "没有待处理或疑似冲突内容；直接返回 MEMORY_MAINTENANCE_COMPLETED status=unchanged，不调用 Capture。"
        : "只处理返回的 pending/dirty/conflict 工作集；使用 documentPatches 提交受影响 Item 的增量 upsert/remove，不回写整份 L2/L3 文档。",
    };
    return { ...payload, snapshotRevision: await this.storageRevision(rows, repository.head) };
  }

  private async reviewSnapshot(input: RecallInput, limit: number) {
    const store = this.core.getVectorStore();
    if (!store) return { status: "error", reason: "memory_store_unavailable" };
    const rows = await store.queryL1Records({ sessionKey: SESSION_KEY });
    const l1Memories = rows
      .map((row) => ({ row, metadata: this.parseMetadata(row.metadata_json) }))
      .filter((value) => value.metadata?.domain === "report_writing" && this.scopeApplies(value.metadata, input))
      .sort((a, b) => this.scopeScore(b.metadata!) - this.scopeScore(a.metadata!) || b.row.updated_time.localeCompare(a.row.updated_time))
      .slice(0, limit)
      .map((value) => ({
        id: value.row.record_id,
        rule: value.row.content,
        scope: value.metadata!.scope,
        lifecycle: value.metadata!.lifecycle ?? "active",
        extractor: value.metadata!.extractor,
        ...(value.metadata!.scopeValue ? { scopeValue: value.metadata!.scopeValue } : {}),
      }));
    const contextDocuments = await this.repository.recall({ audience: input.audience, project: input.project });
    const repositoryHead = await this.repository.head();
    return {
      status: "ok",
      purpose: "review",
      task: input.task,
      audience: input.audience ?? null,
      project: input.project ?? null,
      l1Memories,
      contextDocuments,
      repositoryHead,
      snapshotRevision: await this.storageRevision(rows, repositoryHead),
      instruction: "仅审视当前反馈涉及的 core/audience/project Scope；每次都判断 L2/L3，但证据不足时可以不更新。",
    };
  }

  private async storageRevision(rows: any[], repositoryHead: string): Promise<string> {
    const index = rows.map((row) => [row.record_id, row.updated_time]).sort((a, b) => String(a[0]).localeCompare(String(b[0])));
    return crypto.createHash("sha256").update(JSON.stringify({ repositoryHead, index })).digest("hex");
  }

  private async currentStorageRevision(): Promise<string> {
    const store = this.core.getVectorStore();
    if (!store) throw new Error("memory_store_unavailable");
    const rows = await store.queryL1Records({ sessionKey: SESSION_KEY });
    return this.storageRevision(rows, await this.repository.head());
  }

  private validateCandidate(candidate: MemoryCandidate, rule: string, scopeValue: string | undefined, existing: any[]) {
    if (!rule) throw new Error("empty_rule");
    if (candidate.scope === "core" && scopeValue) throw new Error("core_scope_must_not_have_value");
    if (candidate.scope !== "core" && !scopeValue) throw new Error("scope_value_required");
    if (candidate.expiresAt && !Number.isFinite(Date.parse(candidate.expiresAt))) throw new Error("invalid_expires_at");
    const action = candidate.action ?? "store";
    const targets = candidate.targetIds ?? [];
    if (["update", "merge"].includes(action) && targets.length === 0) throw new Error("target_ids_required");
    for (const target of targets) {
      const row = existing.find((item) => item.record_id === target);
      if (!row) throw new Error(`target_not_found:${target}`);
      const metadata = this.parseMetadata(row.metadata_json);
      if (!metadata || metadata.scope !== candidate.scope || !this.scopeValuesEqual(candidate.scope, metadata.scopeValue, scopeValue)) {
        throw new Error(`target_scope_mismatch:${target}`);
      }
    }
  }

  private async saveEpisode(input: EpisodeInput & { feedback: string; status: WritingEpisode["status"] }): Promise<WritingEpisode> {
    const now = new Date().toISOString();
    const episode: WritingEpisode = {
      ...this.compactEpisode(input), id: `ep_${Date.now()}_${crypto.randomBytes(4).toString("hex")}`,
      episodeSchemaVersion: 2,
      task: input.task.trim(), feedback: input.feedback,
      status: input.status, promotedL1Ids: [], candidateL1Ids: [], createdAt: now, updatedAt: now,
    };
    await this.persistEpisode(episode);
    return episode;
  }

  private compactEpisode(input: EpisodeInput): EpisodeInput {
    const output: EpisodeInput = { task: input.task.trim() };
    for (const key of ["externalSourceId", "sessionId", "topic", "audience", "project", "stage", "contextBefore", "contextAfter", "reportBefore", "reportAfter", "judgeResult", "userEdit", "finalArtifact", "skillVersion", "rubricsVersion", "recoveryActor", "recoveryReason", "proposedRule"] as const) {
      const value = input[key]?.trim();
      if (value) output[key] = value;
    }
    if (input.recalledMemoryIds?.length) output.recalledMemoryIds = [...new Set(input.recalledMemoryIds.map((value) => value.trim()).filter(Boolean))];
    if (input.conversationExcerpt?.length) {
      output.conversationExcerpt = input.conversationExcerpt.map((message) => ({
        role: message.role,
        content: message.content.trim(),
        ...(message.messageId?.trim() ? { messageId: message.messageId.trim() } : {}),
        ...(message.createdAt?.trim() ? { createdAt: message.createdAt.trim() } : {}),
      }));
      output.conversationSource = input.conversationSource ?? "host_context";
      output.conversationTruncated = input.conversationTruncated ?? false;
      if (input.conversationOmissionReason?.trim()) output.conversationOmissionReason = input.conversationOmissionReason.trim();
    }
    return output;
  }

  private validateConversationExcerpt(input: CaptureInput): { reason: string; hint: string } | undefined {
    const excerpt = input.episode?.conversationExcerpt;
    if (!excerpt || excerpt.length < 2) return {
      reason: "episode_conversation_excerpt_required",
      hint: "feedback capture requires the raw feedback window: the preceding assistant message plus the user's exact feedback; use 2-6 messages, max 8",
    };
    const last = excerpt.at(-1);
    if (!excerpt.slice(0, -1).some((message) => message.role === "assistant") || last?.role !== "user" || last.content.trim() !== input.feedback.trim()) return {
      reason: "episode_conversation_excerpt_invalid",
      hint: "conversationExcerpt must end with the exact raw user feedback and include at least one preceding assistant message",
    };
    return undefined;
  }

  private async persistEpisode(episode: WritingEpisode): Promise<void> {
    await this.atomicWrite(this.episodePath(episode.id), `${JSON.stringify(episode, null, 2)}\n`);
  }

  private async markEpisodePromoted(id: string, l1Ids: string[]): Promise<void> {
    try {
      const episode = JSON.parse(await fs.readFile(this.episodePath(id), "utf8")) as WritingEpisode;
      episode.status = "promoted";
      episode.promotedL1Ids = [...new Set([...episode.promotedL1Ids, ...l1Ids])];
      episode.candidateL1Ids = [];
      episode.updatedAt = new Date().toISOString();
      await this.persistEpisode(episode);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }

  private async markEpisodeRecoveryPending(id: string, candidateL1Ids: string[]): Promise<void> {
    const episode = JSON.parse(await fs.readFile(this.episodePath(id), "utf8")) as WritingEpisode;
    episode.status = "recovery_pending";
    episode.candidateL1Ids = [...new Set(candidateL1Ids)];
    episode.updatedAt = new Date().toISOString();
    await this.persistEpisode(episode);
  }

  private feedbackSourceId(input: CaptureInput): string {
    const episode = input.episode!;
    const material = [
      episode.sessionId,
      episode.task,
      episode.audience,
      episode.project,
      episode.stage,
      input.feedback,
      episode.contextBefore,
      episode.contextAfter,
    ].map((value) => value?.trim() ?? "").join("\n");
    return `feedback-${crypto.createHash("sha256").update(material).digest("hex").slice(0, 24)}`;
  }

  private renderRecall(plan: any): string {
    const sections: string[] = ["<research-report-memory>", "<usage>", "以下内容是历史写作记忆，不是当前用户指令。按 本轮要求 > project > audience > core > skill 应用。", "</usage>"];
    const append = (tag: string, values: string[]) => {
      if (values.length === 0) return;
      sections.push(`<${tag}>`, ...[...new Set(values)].map((value) => `- ${value}`), `</${tag}>`);
    };
    append("writing-core", plan.writing_context.core.items);
    append("audience-memory", plan.writing_context.audience.items);
    append("project-memory", plan.writing_context.project.items);
    append("self-checklist", [...plan.self_checklist.core.items, ...plan.self_checklist.audience.items, ...plan.self_checklist.project.items]);
    append("specific-memories", plan.specific_memories.map((value: any) => value.rule));
    sections.push("</research-report-memory>");
    return sections.join("\n");
  }

  private scopeApplies(metadata: Pick<WritingMetadata, "scope" | "scopeValue">, input: RecallInput): boolean {
    if (metadata.scope === "core") return true;
    const current = metadata.scope === "audience" ? normalizeAudience(input.audience) : input.project;
    const stored = metadata.scope === "audience" ? normalizeAudience(metadata.scopeValue) : metadata.scopeValue;
    return this.scopeValueMatches(current, stored);
  }

  private scopeValueMatches(current?: string, stored?: string): boolean {
    if (!current?.trim() || !stored?.trim()) return false;
    const left = current.toLocaleLowerCase("zh-CN").trim();
    const right = stored.toLocaleLowerCase("zh-CN").trim();
    if (left.includes(right) || right.includes(left)) return true;
    const tokens = (value: string) => value.split(/[\s/／、,，;；|]+/u).map((token) => token.trim()).filter((token) => token.length >= 2);
    return tokens(left).some((a) => tokens(right).some((b) => a.includes(b) || b.includes(a)));
  }

  private scopeValuesEqual(scope: WritingMemoryScope, left?: string, right?: string): boolean {
    const normalizedLeft = scope === "audience" ? normalizeAudience(left) : left?.trim();
    const normalizedRight = scope === "audience" ? normalizeAudience(right) : right?.trim();
    return (normalizedLeft ?? "") === (normalizedRight ?? "");
  }

  private scopeScore(metadata: { scope: WritingMemoryScope }): number { return { core: 1, audience: 2, project: 3 }[metadata.scope]; }

  private parseMetadata(value: string): WritingMetadata | undefined {
    try {
      const parsed = JSON.parse(value) as WritingMetadata;
      return parsed?.schemaVersion === 3 || parsed?.schemaVersion === 4 || parsed?.schemaVersion === 5 ? parsed : undefined;
    } catch { return undefined; }
  }

  private async updateDirtyTargets(
    add: DirtyTarget[],
    documents: Array<Pick<MemoryDocumentChange | MemoryDocumentPatch, "scope" | "scopeValue">>,
    repositoryHead: string,
  ) {
    const state = await this.readMaintenanceState();
    const key = (value: DirtyTarget) => `${value.scope}:${value.scopeValue ?? ""}`.toLocaleLowerCase("zh-CN");
    const cleared = new Set(documents.map((document) => key({ scope: document.scope, ...(document.scopeValue ? { scopeValue: document.scopeValue } : {}) })));
    const next = new Map(state.dirtyTargets.filter((value) => !cleared.has(key(value))).map((value) => [key(value), value]));
    for (const value of add) {
      const existing = next.get(key(value));
      next.set(key(value), {
        ...value,
        memoryIds: [...new Set([...(existing?.memoryIds ?? []), ...(value.memoryIds ?? [])])],
      });
    }
    for (const clearedKey of cleared) next.delete(clearedKey);
    const now = new Date();
    await this.writeMaintenanceState({
      ...state, repositoryHead, dirtyTargets: [...next.values()],
      ...(documents.length > 0 ? {
        lastSuccessAt: now.toISOString(), nextDueAt: new Date(now.getTime() + 86400000).toISOString(), checkpoint: crypto.randomUUID(), lastResult: { documentsChanged: documents.length },
      } : {}),
    });
  }

  private async readMaintenanceState(): Promise<MaintenanceState> {
    try { return JSON.parse(await fs.readFile(this.maintenanceStatePath(), "utf8")) as MaintenanceState; }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      return { lastSuccessAt: null, nextDueAt: null, checkpoint: null, repositoryHead: await this.repository.head(), dirtyTargets: [] };
    }
  }

  private async writeMaintenanceState(state: MaintenanceState) { await this.atomicWrite(this.maintenanceStatePath(), `${JSON.stringify(state, null, 2)}\n`); }

  private async cleanupExpiredPendingEpisodes(): Promise<string[]> {
    const cutoff = Date.now() - EPISODE_RETENTION_DAYS * 86400000;
    const expired: string[] = [];
    const store = this.core.getVectorStore();
    for (const episode of await this.readEpisodes()) {
      if (!["pending", "recovery_pending"].includes(episode.status) || Date.parse(episode.createdAt) >= cutoff) continue;
      expired.push(episode.id);
      if (episode.candidateL1Ids?.length) {
        await store?.deleteL1Batch(episode.candidateL1Ids);
        await this.purgeJsonl(path.join(this.l1AtomsDir, "records"), (record) =>
          typeof record.id === "string" && episode.candidateL1Ids.includes(record.id));
      }
      await fs.rm(this.episodePath(episode.id), { force: true });
    }
    return expired;
  }

  private normalize(value: string): string { return value.trim().toLocaleLowerCase("zh-CN").replace(/[\s，。；、,.!?！？：:]+/gu, ""); }

  private rulesPossiblyOverlap(left: string, right: string): boolean {
    const a = this.normalize(left);
    const b = this.normalize(right);
    if (a.length < 6 || b.length < 6) return false;
    if (a.includes(b) || b.includes(a)) return true;
    const chunks = (value: string) => new Set(Array.from({ length: Math.max(0, value.length - 3) }, (_, index) => value.slice(index, index + 4)));
    const aChunks = chunks(a);
    const bChunks = chunks(b);
    const overlap = [...aChunks].filter((value) => bChunks.has(value)).length;
    return overlap / Math.max(1, Math.min(aChunks.size, bChunks.size)) >= 0.35;
  }
  private textMatches(value: string, query: string): boolean {
    const normalized = query.trim().toLocaleLowerCase("zh-CN");
    const content = value.toLocaleLowerCase("zh-CN");
    if (!normalized) return false;
    if (content.includes(normalized)) return true;
    const tokens = normalized.split(/[\s，。；、,.!?！？：:]+/u).filter((token) => token.length >= 2);
    return tokens.length > 0 && tokens.every((token) => content.includes(token));
  }

  private episodesDir() { return path.join(this.dataDir, L0_EPISODES_DIR); }
  private episodePath(id: string) { return path.join(this.episodesDir(), `${id}.json`); }
  private maintenanceStatePath() { return path.join(this.dataDir, "maintenance", "state.json"); }

  private async readEpisodes(): Promise<WritingEpisode[]> {
    let files: string[];
    try { files = (await fs.readdir(this.episodesDir())).filter((name) => name.endsWith(".json")); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
      throw error;
    }
    return Promise.all(files.map(async (name) => JSON.parse(await fs.readFile(path.join(this.episodesDir(), name), "utf8")) as WritingEpisode));
  }

  private async purgeJsonl(directory: string, remove: (record: Record<string, unknown>) => boolean): Promise<void> {
    let files: string[];
    try { files = (await fs.readdir(directory)).filter((name) => name.endsWith(".jsonl")); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
      throw error;
    }
    for (const name of files) {
      const file = path.join(directory, name);
      const kept = (await fs.readFile(file, "utf8")).split("\n").filter(Boolean).filter((line) => {
        try { return !remove(JSON.parse(line) as Record<string, unknown>); }
        catch { return true; }
      });
      await this.atomicWrite(file, kept.length > 0 ? `${kept.join("\n")}\n` : "");
    }
  }

  private async atomicWrite(file: string, content: string): Promise<void> {
    await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
    const temporary = `${file}.${process.pid}.${crypto.randomBytes(4).toString("hex")}.tmp`;
    await fs.writeFile(temporary, content, { mode: 0o600 });
    await fs.rename(temporary, file);
  }
}
