import fs from "node:fs/promises";
import path from "node:path";

export const L0_L1_MEMORY_DIR = "l0-l1-memory";
export const L0_EPISODES_DIR = path.join(L0_L1_MEMORY_DIR, "l0-episodes");
export const L1_ATOMS_DIR = path.join(L0_L1_MEMORY_DIR, "l1-atoms");
export const MEMORYCORE_DIR = path.join(L0_L1_MEMORY_DIR, "memorycore");
export const L2_L3_MEMORY_DIR = "l2-l3-memory";

const MEMORYCORE_INTERNAL_ENTRIES = [
  "conversations",
  "scene_blocks",
  ".metadata",
  ".backup",
  "vectors.db",
  "vectors.db-shm",
  "vectors.db-wal",
];

async function exists(target: string): Promise<boolean> {
  try { await fs.access(target); return true; }
  catch { return false; }
}

export async function migrateStorageLayout(dataDir: string, options: { allowLegacyMigration?: boolean } = {}): Promise<void> {
  await fs.mkdir(dataDir, { recursive: true, mode: 0o700 });
  const moves = [
    { from: "episodes", to: L0_EPISODES_DIR },
    { from: "l0-writing-episodes", to: L0_EPISODES_DIR },
    { from: "records", to: path.join(L1_ATOMS_DIR, "records") },
    { from: path.join("memorycore-l0-l1", "records"), to: path.join(L1_ATOMS_DIR, "records") },
    ...MEMORYCORE_INTERNAL_ENTRIES.flatMap((name) => [
      { from: name, to: path.join(MEMORYCORE_DIR, name) },
      { from: path.join("memorycore-l0-l1", name), to: path.join(MEMORYCORE_DIR, name) },
    ]),
    { from: "repositories", to: L2_L3_MEMORY_DIR },
    { from: "worktrees", to: path.join(L2_L3_MEMORY_DIR, "worktrees") },
  ];
  const pending: string[] = [];
  for (const move of moves) {
    if (await exists(path.join(dataDir, move.from))) pending.push(move.from);
  }
  if (pending.length > 0 && options.allowLegacyMigration === false) {
    throw new Error(`storage_layout_migration_deferred:${pending.join(",")}`);
  }
  for (const move of moves) {
    const source = path.join(dataDir, move.from);
    const target = path.join(dataDir, move.to);
    if (!(await exists(source))) continue;
    if (await exists(target)) throw new Error(`storage_layout_migration_conflict:${move.from}`);
    await fs.mkdir(path.dirname(target), { recursive: true, mode: 0o700 });
    await fs.rename(source, target);
  }
  await fs.rmdir(path.join(dataDir, "memorycore-l0-l1")).catch((error: NodeJS.ErrnoException) => {
    if (error.code !== "ENOENT" && error.code !== "ENOTEMPTY") throw error;
  });
  await fs.mkdir(path.join(dataDir, L0_EPISODES_DIR), { recursive: true, mode: 0o700 });
  await fs.mkdir(path.join(dataDir, L1_ATOMS_DIR), { recursive: true, mode: 0o700 });
  await fs.mkdir(path.join(dataDir, MEMORYCORE_DIR), { recursive: true, mode: 0o700 });
  await fs.mkdir(path.join(dataDir, L2_L3_MEMORY_DIR), { recursive: true, mode: 0o700 });
}
