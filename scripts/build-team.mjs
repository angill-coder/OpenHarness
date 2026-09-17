import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceManifest = JSON.parse(
  fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"),
);
const version = sourceManifest.version;
const pluginName = sourceManifest.name;
const releaseRoot = path.join(root, "release");
const stagingName = `${pluginName}-${version}-${process.platform}-${process.arch}`;
const stagingDir = path.join(releaseRoot, stagingName, "plugins", pluginName);
const teamName = `${pluginName}-${version}`;
const teamDir = path.join(releaseRoot, teamName);
const zipPath = `${teamDir}.zip`;
const noArchive = process.argv.includes("--no-archive");
const keepStaging = process.argv.includes("--keep-staging");

const LEAD_AGENT = sourceManifest.teamInfo.leadAgent;
const MEMBER_AGENTS = sourceManifest.teamInfo.memberAgents;

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    encoding: "utf8",
    stdio: "inherit",
    ...options,
  });
  if (result.status !== 0) throw new Error(`${command} failed with exit code ${result.status}`);
}

function writeJson(target, value) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${JSON.stringify(value, null, 2)}\n`);
}

run(process.execPath, ["scripts/build-release.mjs", "--no-archive"]);
if (!fs.statSync(stagingDir, { throwIfNoEntry: false })?.isDirectory()) {
  throw new Error(`Team staging release not found: ${stagingDir}`);
}

fs.rmSync(teamDir, { recursive: true, force: true });
fs.rmSync(zipPath, { force: true });
fs.rmSync(`${zipPath}.sha256`, { force: true });
fs.cpSync(stagingDir, teamDir, { recursive: true });

// Spec §2.2: Team package keeps agents/ skills/ avatars/ bin/ settings.json at plugin root.
// docs/ is development-only; hooks/ is flattened to hooks.json (see below).
fs.rmSync(path.join(teamDir, "docs"), { recursive: true, force: true });
fs.rmSync(path.join(teamDir, "hooks"), { recursive: true, force: true });
fs.rmSync(path.join(teamDir, "dist/capture-checkpoint.mjs"), { force: true });
for (const unused of [
  "run-node.sh",
  "run-node.cmd",
]) {
  fs.rmSync(path.join(teamDir, "scripts", unused), { force: true });
}

fs.mkdirSync(path.join(teamDir, "bin"), { recursive: true });
fs.copyFileSync(path.join(root, "scripts/run-node.sh"), path.join(teamDir, "bin/run-node"));
fs.copyFileSync(path.join(root, "scripts/run-node.cmd"), path.join(teamDir, "bin/run-node.cmd"));
fs.copyFileSync(
  path.join(stagingDir, "dist/capture-checkpoint.mjs"),
  path.join(teamDir, "bin/capture-checkpoint.mjs"),
);
fs.chmodSync(path.join(teamDir, "bin/run-node"), 0o755);


const hookCommand = process.platform === "win32"
  ? 'cmd.exe /d /c call "${CODEBUDDY_PLUGIN_ROOT}\\bin\\run-node.cmd" '
    + '"${CODEBUDDY_PLUGIN_ROOT}\\bin\\capture-checkpoint.mjs"'
  : 'sh "${CODEBUDDY_PLUGIN_ROOT}/bin/run-node" '
    + '"${CODEBUDDY_PLUGIN_ROOT}/bin/capture-checkpoint.mjs"';
const hooks = {
  description: "Report Loop launcher and writing-feedback capture reminder.",
  hooks: Object.fromEntries(["UserPromptSubmit", "PostToolUse", "Stop"].map((event) => [event, [{
    matcher: "*",
    hooks: [{
      type: "command",
      command: `${hookCommand} ${event === "UserPromptSubmit" ? "prompt" : event === "PostToolUse" ? "post-tool" : "stop"}`,
      timeout: 5,
    }],
  }]])),
};
writeJson(path.join(teamDir, "hooks.json"), hooks);

const teamManifest = {
  ...sourceManifest,
  hooks: "./hooks.json",
};
writeJson(path.join(teamDir, ".codebuddy-plugin/plugin.json"), teamManifest);

const buildInfoPath = path.join(teamDir, "BUILD-INFO.json");
const buildInfo = JSON.parse(fs.readFileSync(buildInfoPath, "utf8"));
delete buildInfo.platform;
delete buildInfo.arch;
writeJson(buildInfoPath, {
  ...buildInfo,
  name: pluginName,
  packageType: "workbuddy-expert",
  expertType: "team",
  supportedPlatforms: ["darwin", "win32"],
  crossPlatformLauncher: "bin/run-node + bin/run-node.cmd",
});

// ---- Spec §10.1 consistency gate ----
const errors = [];
const at = (rel) => path.join(teamDir, rel);
const exists = (rel) => fs.existsSync(at(rel));

// Forbidden directories at package root
for (const name of ["hooks", "commands", "expert", ".codex-plugin"]) {
  if (exists(name)) errors.push(`Forbidden directory in Team package: ${name}`);
}

// Required files
for (const rel of [".codebuddy-plugin/plugin.json", "settings.json", "agents", "avatars"]) {
  if (!exists(rel)) errors.push(`Missing required path: ${rel}`);
}

// ---- Loop launch path (hooks are the ONLY way to start the Report Loop) ----
// WorkBuddy Team experts do load hooks.json — confirmed with the platform side.
// Since the MCP fallback was removed, these checks guard the single launch path:
// a broken hooks.json means the Loop silently never starts.
if (!exists("hooks.json")) {
  errors.push("Missing hooks.json — the Report Loop would never start");
} else {
  const registered = JSON.parse(fs.readFileSync(at("hooks.json"), "utf8"));
  if (teamManifest.hooks !== "./hooks.json") {
    errors.push(`plugin.json hooks must be "./hooks.json" (got ${teamManifest.hooks})`);
  }
  // PostToolUse is what detects the Job write and spawns the Python Runner.
  for (const event of ["UserPromptSubmit", "PostToolUse", "Stop"]) {
    const entries = registered.hooks?.[event];
    if (!Array.isArray(entries) || entries.length === 0) {
      errors.push(`hooks.json is missing the ${event} registration`);
      continue;
    }
    const command = entries[0].hooks?.[0]?.command ?? "";
    if (!command.includes("capture-checkpoint.mjs")) {
      errors.push(`${event} hook does not invoke capture-checkpoint.mjs`);
    }
    // The packaged hook lives in bin/, not scripts/ or dist/
    if (!command.includes("bin/capture-checkpoint.mjs")
        && !command.includes("bin\\capture-checkpoint.mjs")) {
      errors.push(`${event} hook must point at bin/capture-checkpoint.mjs`);
    }
  }
  // Every launcher referenced by a hook command must actually ship.
  for (const rel of ["bin/capture-checkpoint.mjs", "bin/run-node", "bin/run-node.cmd"]) {
    if (!exists(rel)) errors.push(`Hook launcher missing from package: ${rel}`);
  }
  // The hook bundle must be self-contained: no MCP server, no node_modules.
  const bundled = fs.readFileSync(at("bin/capture-checkpoint.mjs"), "utf8");
  const externalImports = [...bundled.matchAll(/^import .*? from "([^"]+)";$/gmu)]
    .map((match) => match[1])
    .filter((specifier) => !specifier.startsWith("node:"));
  if (externalImports.length > 0) {
    errors.push(`Hook bundle has external imports: ${externalImports.join(", ")}`);
  }
  if (!/run-python/u.test(bundled)) {
    errors.push("Hook bundle does not contain the Python Runner launcher");
  }
  // Python Runner and its launcher must ship alongside.
  for (const rel of ["report_loop/runner.py", "scripts/run-python.sh", "scripts/run-python.cmd"]) {
    if (!exists(rel)) errors.push(`Report Loop runtime missing from package: ${rel}`);
  }
}

// MCP was removed entirely; the Loop starts through hooks only.
if (exists(".mcp.json")) errors.push("Unexpected .mcp.json — the Loop no longer uses MCP");
if (exists("dist/memory-server.mjs")) errors.push("Unexpected MCP server bundle in dist/");
if (teamManifest.dependencies) {
  errors.push("plugin.json must not declare dependencies; the Loop needs no MCP");
}

// settings.json agent === agentName === leadAgent
const settings = JSON.parse(fs.readFileSync(at("settings.json"), "utf8"));
if (settings.agent !== teamManifest.agentName) {
  errors.push(`settings.json agent (${settings.agent}) !== plugin.json agentName (${teamManifest.agentName})`);
}
if (teamManifest.agentName !== LEAD_AGENT) {
  errors.push(`agentName (${teamManifest.agentName}) !== teamInfo.leadAgent (${LEAD_AGENT})`);
}
if (teamManifest.expertType !== "team") errors.push(`expertType must be "team"`);
if (teamManifest.plugin !== teamManifest.name) {
  errors.push(`plugin (${teamManifest.plugin}) !== name (${teamManifest.name})`);
}

// Lead agent filename must be prefixed, not generic team-lead
if (LEAD_AGENT === "team-lead") errors.push("Lead agent must not use the generic name team-lead");

// Every declared agent file exists, frontmatter name matches filename, no tools field
const declaredNames = new Set();
for (const rel of teamManifest.agents) {
  const relPath = rel.replace(/^\.\//u, "");
  if (!exists(relPath)) { errors.push(`Declared agent file missing: ${relPath}`); continue; }
  const content = fs.readFileSync(at(relPath), "utf8");
  const frontmatter = content.match(/^---\n([\s\S]*?)\n---/u)?.[1] ?? "";
  if (!frontmatter) { errors.push(`Agent has no frontmatter: ${relPath}`); continue; }
  if (/^tools:/mu.test(frontmatter)) errors.push(`Agent declares forbidden tools field: ${relPath}`);
  const fmName = frontmatter.match(/^name:\s*(\S+)/mu)?.[1];
  const fileName = path.basename(relPath, ".md");
  if (fmName !== fileName) errors.push(`Agent name (${fmName}) !== filename (${fileName})`);
  if (!/^description:/mu.test(frontmatter)) errors.push(`Agent missing description: ${relPath}`);
  for (const field of ["displayName", "profession"]) {
    if (!new RegExp(`^${field}:`, "mu").test(frontmatter)) {
      errors.push(`Agent missing ${field}: ${relPath}`);
    }
  }
  declaredNames.add(fileName);
}

// teamInfo lead + members all declared in agents[]
for (const id of [LEAD_AGENT, ...MEMBER_AGENTS]) {
  if (!declaredNames.has(id)) errors.push(`teamInfo references undeclared agent: ${id}`);
}

// members[] must cover lead + members exactly, with existing avatars
const memberIds = teamManifest.members.map((m) => m.id);
const expectedIds = [LEAD_AGENT, ...MEMBER_AGENTS];
for (const id of expectedIds) {
  if (!memberIds.includes(id)) errors.push(`members[] missing entry for: ${id}`);
}
for (const id of memberIds) {
  if (!expectedIds.includes(id)) errors.push(`members[] has entry not in teamInfo: ${id}`);
}
const leads = teamManifest.members.filter((m) => m.role === "lead");
if (leads.length !== 1) errors.push(`members[] must contain exactly one role=lead (found ${leads.length})`);
else if (leads[0].id !== LEAD_AGENT) errors.push(`members[] lead (${leads[0].id}) !== leadAgent (${LEAD_AGENT})`);

for (const m of teamManifest.members) {
  for (const field of ["id", "name", "profession", "avatar", "role"]) {
    if (!m[field]) errors.push(`members[${m.id}] missing ${field}`);
  }
  if (m.avatar && !exists(m.avatar)) errors.push(`members[${m.id}] avatar missing: ${m.avatar}`);
  for (const field of ["name", "profession"]) {
    if (m[field] && (!m[field].en || !m[field].zh)) {
      errors.push(`members[${m.id}].${field} needs both en and zh`);
    }
  }
}

// Team avatar
if (!exists(teamManifest.avatar)) errors.push(`avatar missing: ${teamManifest.avatar}`);

// Bilingual display fields
for (const field of ["displayName", "profession", "displayDescription", "defaultInitPrompt"]) {
  const value = teamManifest[field];
  if (!value?.en || !value?.zh) errors.push(`${field} needs both en and zh`);
}

// Spec §3.3: Team profession must equal displayName
if (JSON.stringify(teamManifest.profession) !== JSON.stringify(teamManifest.displayName)) {
  errors.push("Team profession must match displayName");
}

// Spec §3.3: displayDescription zh must be 40-50 chars
const zhLen = [...(teamManifest.displayDescription?.zh ?? "")].length;
if (zhLen < 40 || zhLen > 50) errors.push(`displayDescription.zh must be 40-50 chars (got ${zhLen})`);

// tags and quickPrompts: fixed 3, bilingual
for (const field of ["tags", "quickPrompts"]) {
  const list = teamManifest[field];
  if (!Array.isArray(list) || list.length !== 3) {
    errors.push(`${field} must have exactly 3 entries (got ${list?.length})`);
    continue;
  }
  list.forEach((item, i) => {
    if (!item.en || !item.zh) errors.push(`${field}[${i}] needs both en and zh`);
  });
}

// defaultInitPrompt must equal quickPrompts[0]
if (JSON.stringify(teamManifest.defaultInitPrompt) !== JSON.stringify(teamManifest.quickPrompts?.[0])) {
  errors.push("defaultInitPrompt must match quickPrompts[0]");
}

// categoryId in allowed set
const CATEGORY_IDS = new Set([
  "01-ProductDesign", "02-Engineering", "03-GameSpatial", "04-DataAI",
  "05-MarketingGrowth", "06-ContentCreative", "07-SalesCommerce",
  "08-FinanceInvestment", "09-OperationsHR", "10-ProjectQuality",
  "11-SecurityCompliance", "12-IndustryConsultant", "13-TencentZone",
  "14-WorldWise", "15-Education",
]);
if (!CATEGORY_IDS.has(teamManifest.categoryId)) {
  errors.push(`Invalid categoryId: ${teamManifest.categoryId}`);
}

// name format + author
if (!/^[a-z][a-z0-9-]*$/u.test(teamManifest.name)) {
  errors.push(`name must be lowercase letters/digits/hyphens: ${teamManifest.name}`);
}
if (!/^\d+\.\d+\.\d+/u.test(teamManifest.version)) {
  errors.push(`version must be semantic: ${teamManifest.version}`);
}
for (const field of ["name", "email"]) {
  if (!teamManifest.author?.[field]) errors.push(`author.${field} is required`);
}

// skills[] each has SKILL.md
for (const rel of teamManifest.skills ?? []) {
  const skillMd = path.join(rel.replace(/^\.\//u, ""), "SKILL.md");
  if (!exists(skillMd)) errors.push(`Skill missing SKILL.md: ${skillMd}`);
}

// Lead agent body must contain the mandatory collaboration ironclad rules (spec §5.2.1)
const leadBody = fs.readFileSync(at(`agents/${LEAD_AGENT}.md`), "utf8");
for (const required of ["团队协作机制（铁律）", "建立团队", "调度成员", "消息中转", "成员结论为准", "严禁行为"]) {
  if (!leadBody.includes(required)) errors.push(`Lead agent missing required section: ${required}`);
}
// Lead must list every member
for (const id of MEMBER_AGENTS) {
  if (!leadBody.includes(id)) errors.push(`Lead agent body does not list member: ${id}`);
}

// Avatar format/size checks (spec §8.1)
for (const file of fs.readdirSync(at("avatars"))) {
  const full = at(path.join("avatars", file));
  const size = fs.statSync(full).size;
  if (size > 500 * 1024) {
    errors.push(`Avatar exceeds 500KB: ${file} (${Math.round(size / 1024)}KB)`);
  }
  if (file.toLowerCase().endsWith(".png")) {
    const buf = fs.readFileSync(full);
    const width = buf.readUInt32BE(16);
    const height = buf.readUInt32BE(20);
    if (width !== 512 || height !== 512) {
      errors.push(`Avatar must be 512x512: ${file} (${width}x${height})`);
    }
  } else if (!/\.jpe?g$/iu.test(file)) {
    errors.push(`Avatar must be PNG or JPG: ${file}`);
  }
}

// No hardcoded secrets in declared MCP config
const mcpJson = JSON.stringify(teamManifest.dependencies ?? {});
if (/(?:sk-|ghp_|AKIA)[A-Za-z0-9]{8,}/u.test(mcpJson)) {
  errors.push("Possible hardcoded token in MCP declaration");
}

if (errors.length > 0) {
  throw new Error(`Team package validation failed:\n  - ${errors.join("\n  - ")}`);
}

if (!noArchive) {
  if (process.platform === "win32") {
    run("powershell.exe", [
      "-NoProfile",
      "-Command",
      `Compress-Archive -LiteralPath '${teamDir.replaceAll("'", "''")}' -DestinationPath '${zipPath.replaceAll("'", "''")}' -Force`,
    ]);
  } else {
    run("zip", ["-qry", path.basename(zipPath), teamName], { cwd: releaseRoot });
  }
  const digest = crypto.createHash("sha256").update(fs.readFileSync(zipPath)).digest("hex");
  fs.writeFileSync(`${zipPath}.sha256`, `${digest}  ${path.basename(zipPath)}\n`);
}

// Staging is a build-only intermediate consumed solely by this script. Leaving
// it behind makes release/ look like it holds two shippable packages, which is
// how the wrong directory gets submitted. Remove it once validation passed.
const stagingRoot = path.join(releaseRoot, stagingName);
if (!keepStaging && fs.existsSync(stagingRoot)) {
  fs.rmSync(stagingRoot, { recursive: true, force: true });
}

process.stdout.write(`WorkBuddy Team expert built: ${teamDir}\n`);
process.stdout.write(`  lead:    ${LEAD_AGENT}\n`);
process.stdout.write(`  members: ${MEMBER_AGENTS.join(", ")}\n`);
if (!noArchive) process.stdout.write(`  archive: ${zipPath}\n`);
if (keepStaging) process.stdout.write(`  staging: ${stagingRoot}\n`);
