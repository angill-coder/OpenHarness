import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.resolve(import.meta.dirname, "../..");

test("integrated Skill enables memory by default and delegates capture unless disabled", () => {
  const skill = fs.readFileSync(path.join(root, "skills/research-report-loop/SKILL.md"), "utf8");
  const orchestration = fs.readFileSync(
    path.join(root, "skills/research-report-loop/references/memory-orchestration.md"),
    "utf8",
  );
  assert.match(skill, /默认启用的长期写作记忆/u);
  assert.match(skill, /Memory 已开启时[\s\S]*operation=capture/u);
  assert.match(skill, /写作前不要 Recall Memory/u);
  assert.match(orchestration, /默认启用/u);
  assert.match(orchestration, /operation=settings/u);
  assert.match(orchestration, /关闭时.*Base Rubrics/u);
  assert.match(orchestration, /operation=capture/u);
  assert.match(orchestration, /主理人不直接读写记忆文件/u);
  assert.match(orchestration, /不得修改.*Base Rubric/su);
  assert.match(orchestration, /MEMORY_CAPTURE_COMPLETED.*MEMORY_CAPTURE_FAILED/su);
});

test("memory agent keeps the Layer, Scope, and markdown storage contract", () => {
  const agent = fs.readFileSync(path.join(root, "agents/report-memory-agent.md"), "utf8");
  const frontmatter = agent.match(/^---\r?\n([\s\S]*?)\r?\n---/u)?.[1] ?? "";
  // Spec §4.2: Team agents must not declare tools; permissions are system-assigned
  assert.doesNotMatch(frontmatter, /^tools:/mu);
  assert.doesNotMatch(frontmatter, /^(?:model|effort):/mu);

  // 五种 operation 契约
  for (const operation of ["resolve", "inspect_sources", "capture", "manage", "reflect", "settings"]) {
    assert.match(agent, new RegExp(`operation=${operation}`, "u"), operation);
  }

  // 可见的 Markdown 存储，与插件安装目录无关
  assert.match(agent, /ReportAgentMemory\//u);
  assert.match(agent, /与宿主产品和插件安装目录无关/u);
  assert.match(agent, /MEMORY\.md/u);
  assert.match(agent, /memory-history\.md/u);
  assert.match(agent, /L0-episodes\//u);
  assert.match(agent, /L1-atoms\//u);
  assert.match(agent, /不维护全量 L0\/L1 索引或 MEMORY 快照/u);
  assert.match(agent, /不提供历史回滚/u);

  // 三层 + Scope
  assert.match(agent, /L0 Writing Episode/u);
  assert.match(agent, /L1 Atom Memory/u);
  assert.match(agent, /L2B Memory Rubric/u);
  assert.match(agent, /`core \/ audience \/ project` 三选一/u);
  assert.match(agent, /当前任务的 audience\/project 元数据不能反推 Scope/u);

  // revision 与幂等
  assert.match(agent, /revision: N/u);
  assert.match(agent, /持久化修改成功才推进版本，无文件变化不递增/u);
  assert.match(agent, /captureId/u);
  assert.match(agent, /idempotent/u);

  // Reflection 不依赖后台调度
  assert.match(agent, /不依赖后台调度/u);
  assert.match(agent, /MEMORY_CAPTURE_COMPLETED/u);
  assert.match(agent, /MEMORY_CAPTURE_FAILED/u);
  assert.match(agent, /MEMORY_SOURCE_CONFLICT/u);
});

test("plugin declares the team agents, no MCP, and markdown memory", () => {
  const plugin = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"));
  const marketplace = JSON.parse(fs.readFileSync(path.join(root, ".codebuddy-plugin/marketplace.json"), "utf8"));
  const packageManifest = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
  assert.equal(plugin.name, "report-agent-v3");
  assert.deepEqual(plugin.agents, [
    "./agents/report-team-lead.md",
    "./agents/report-evidence-agent.md",
    "./agents/report-writer.md",
    "./agents/report-reviewer.md",
    "./agents/report-memory-agent.md",
  ]);
  assert.equal(plugin.version, packageManifest.version);
  // 本地市场索引必须与插件清单同源，否则安装出的描述与实际不符
  assert.equal(marketplace.plugins[0].version, packageManifest.version);
  assert.equal(marketplace.plugins[0].name, plugin.name);
  assert.equal(marketplace.plugins[0].description, plugin.description);
  assert.equal(marketplace.plugins[0].source, `./plugins/${plugin.name}`);
  assert.equal(marketplace.owner.name, plugin.author.name);

  // 打包产物里的 package.json 是另生成的精简运行时声明，不是源码那份
  assert.equal(packageManifest.name, plugin.name);
  const builder = fs.readFileSync(path.join(root, "scripts/build-release.mjs"), "utf8");
  assert.match(builder, /\$\{pluginName\}-runtime/u);

  // Loop 由 Hook 直接启动：不声明 MCP，也没有 MCP server 源码
  assert.equal(fs.existsSync(path.join(root, ".mcp.json")), false);
  assert.equal(fs.existsSync(path.join(root, "mcp/src")), false);
  assert.equal(plugin.dependencies, undefined);
  const hook = fs.readFileSync(path.join(root, "hooks/capture-checkpoint.mjs"), "utf8");
  assert.match(hook, /ReportLoopLauncher/u);
  assert.match(hook, /from "\.\/lib\/report-loop-launcher\.ts"/u);

  // 记忆改为纯 Markdown 后，数据库/向量检索/MCP 三类依赖都不再需要。
  // 用白名单而非逐个点名，这样将来任何新依赖都必须显式说明理由。
  assert.deepEqual(Object.keys(packageManifest.dependencies).sort(), ["tsx", "zod"]);

  // 旧存储的死代码不得复活
  for (const gone of [
    "mcp/src",
    ".mcp.json",
    "scripts/build-stubs",
    "scripts/verify-mcp-contract.mjs",
    "scripts/register-workbuddy-local.mjs",
    "scripts/migrate-rubric-scope-paths.mjs",
    "prompts",
  ]) {
    assert.equal(fs.existsSync(path.join(root, gone)), false, `${gone} should stay removed`);
  }
});
