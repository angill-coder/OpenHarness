import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

const sourceManifest = JSON.parse(
  fs.readFileSync(path.join(root, ".codebuddy-plugin/plugin.json"), "utf8"),
);
const LEAD_AGENT = "report-team-lead";
const MEMBER_AGENTS = [
  "report-evidence-agent",
  "report-writer",
  "report-reviewer",
  "report-memory-agent",
];

test("source manifest declares a spec-compliant Team expert", () => {
  assert.equal(sourceManifest.expertType, "team");
  assert.equal(sourceManifest.agentName, LEAD_AGENT);
  assert.equal(sourceManifest.teamInfo.leadAgent, LEAD_AGENT);
  assert.deepEqual(sourceManifest.teamInfo.memberAgents, MEMBER_AGENTS);
  assert.equal(sourceManifest.plugin, sourceManifest.name);

  // Spec §5.2: lead filename must be prefixed, never the generic team-lead
  assert.notEqual(LEAD_AGENT, "team-lead");

  // Spec §3.3: Team profession must match displayName
  assert.deepEqual(sourceManifest.profession, sourceManifest.displayName);

  // Spec §3.3: displayDescription zh is 40-50 chars
  const zhLength = [...sourceManifest.displayDescription.zh].length;
  assert.ok(zhLength >= 40 && zhLength <= 50, `displayDescription.zh length ${zhLength}`);

  // Spec §3.3: exactly 3 tags and 3 quickPrompts, all bilingual
  assert.equal(sourceManifest.tags.length, 3);
  assert.equal(sourceManifest.quickPrompts.length, 3);
  for (const item of [...sourceManifest.tags, ...sourceManifest.quickPrompts]) {
    assert.ok(item.en && item.zh);
  }
  assert.deepEqual(sourceManifest.defaultInitPrompt, sourceManifest.quickPrompts[0]);

  // members[] covers lead + every member, exactly one lead
  const memberIds = sourceManifest.members.map((m: { id: string }) => m.id);
  assert.deepEqual(memberIds.slice().sort(), [LEAD_AGENT, ...MEMBER_AGENTS].slice().sort());
  const leads = sourceManifest.members.filter((m: { role: string }) => m.role === "lead");
  assert.equal(leads.length, 1);
  assert.equal(leads[0].id, LEAD_AGENT);

  // every member has complete bilingual display info and an existing avatar
  for (const member of sourceManifest.members) {
    assert.ok(member.name.en && member.name.zh, `${member.id} name`);
    assert.ok(member.profession.en && member.profession.zh, `${member.id} profession`);
    assert.equal(fs.existsSync(path.join(root, member.avatar)), true, `${member.id} avatar`);
  }
  assert.equal(fs.existsSync(path.join(root, sourceManifest.avatar)), true);
});

test("settings.json designates the team lead", () => {
  const settings = JSON.parse(fs.readFileSync(path.join(root, "settings.json"), "utf8"));
  assert.equal(settings.agent, LEAD_AGENT);
  assert.equal(settings.agent, sourceManifest.agentName);
});

test("every declared agent file is spec compliant and declares no tools", () => {
  const declared = sourceManifest.agents.map((rel: string) => rel.replace(/^\.\//u, ""));
  assert.equal(declared.length, 1 + MEMBER_AGENTS.length);

  for (const relative of declared) {
    const content = fs.readFileSync(path.join(root, relative), "utf8");
    const frontmatter = content.match(/^---\r?\n([\s\S]*?)\r?\n---/u)?.[1] ?? "";
    assert.ok(frontmatter, `${relative} has frontmatter`);

    // Spec §4.2: tools is forbidden — declaring it fails review
    assert.doesNotMatch(frontmatter, /^tools:/mu, `${relative} must not declare tools`);

    const name = frontmatter.match(/^name:\s*(\S+)/mu)?.[1];
    assert.equal(name, path.basename(relative, ".md"), `${relative} name matches filename`);
    assert.match(frontmatter, /^description:/mu, `${relative} has description`);
    assert.match(frontmatter, /^displayName:/mu, `${relative} has displayName`);
    assert.match(frontmatter, /^profession:/mu, `${relative} has profession`);
  }
});

test("team lead prompt carries the mandatory collaboration ironclad rules", () => {
  const lead = fs.readFileSync(path.join(root, `agents/${LEAD_AGENT}.md`), "utf8");

  // Spec §5.2.1: these sections are mandatory in every team lead prompt
  for (const section of [
    "团队协作机制（铁律）",
    "建立团队",
    "调度成员",
    "消息中转",
    "成员结论为准",
    "严禁行为",
  ]) {
    assert.ok(lead.includes(section), `lead prompt includes ${section}`);
  }

  // TeamCreate must be lead-only, and the lead must not spawn itself
  assert.match(lead, /TeamCreate/u);
  assert.match(lead, /严禁委派任何成员创建团队/u);
  assert.match(lead, /禁止 spawn 主理人自己/u);
  assert.match(lead, /禁止自己代写任何团队成员的专业产出/u);

  // Spec §5.2: lead must enumerate every member
  for (const member of MEMBER_AGENTS) {
    assert.ok(lead.includes(member), `lead prompt lists ${member}`);
  }

  // Report Loop stays an automation component, not a fabricated member
  assert.doesNotMatch(lead, /judge-[a-z]+ 成员/u);
});

test("declared skills resolve to real SKILL.md files", () => {
  for (const relative of sourceManifest.skills) {
    const skillMd = path.join(root, relative.replace(/^\.\//u, ""), "SKILL.md");
    assert.equal(fs.existsSync(skillMd), true, `${relative} has SKILL.md`);
  }
});

test("avatars meet the 512x512 PNG/JPG size limits", () => {
  const avatarDir = path.join(root, "avatars");
  const files = fs.readdirSync(avatarDir);
  assert.ok(files.length >= 1 + MEMBER_AGENTS.length);

  for (const file of files) {
    const full = path.join(avatarDir, file);
    const size = fs.statSync(full).size;
    assert.ok(size <= 500 * 1024, `${file} is ${Math.round(size / 1024)}KB, limit 500KB`);

    if (file.toLowerCase().endsWith(".png")) {
      const buffer = fs.readFileSync(full);
      assert.equal(buffer.readUInt32BE(16), 512, `${file} width`);
      assert.equal(buffer.readUInt32BE(20), 512, `${file} height`);
    } else {
      assert.match(file, /\.jpe?g$/iu, `${file} must be PNG or JPG`);
    }
  }
});

test("packaged Team expert launches the Loop through a self-contained hook", async () => {
  const completed = spawnSync(
    process.execPath,
    [path.join(root, "scripts/build-team.mjs"), "--no-archive"],
    { cwd: root, encoding: "utf8" },
  );
  assert.equal(completed.status, 0, completed.stderr);

  const teamDir = path.join(root, "release", `${sourceManifest.name}-${sourceManifest.version}`);
  const manifest = JSON.parse(
    fs.readFileSync(path.join(teamDir, ".codebuddy-plugin/plugin.json"), "utf8"),
  );
  const hooks = JSON.parse(fs.readFileSync(path.join(teamDir, "hooks.json"), "utf8"));

  // Spec §2.2 layout: these live at the package root
  for (const required of ["settings.json", "agents", "avatars", "skills", "bin"]) {
    assert.equal(fs.existsSync(path.join(teamDir, required)), true, `packaged ${required}`);
  }
  // Forbidden in the shipped package
  for (const forbidden of ["hooks", "commands", "expert", ".codex-plugin", "docs"]) {
    assert.equal(fs.existsSync(path.join(teamDir, forbidden)), false, `packaged must omit ${forbidden}`);
  }

  assert.equal(manifest.expertType, "team");
  assert.equal(manifest.agentName, LEAD_AGENT);
  assert.deepEqual(manifest.teamInfo.memberAgents, MEMBER_AGENTS);
  const packagedSettings = JSON.parse(fs.readFileSync(path.join(teamDir, "settings.json"), "utf8"));
  assert.equal(packagedSettings.agent, manifest.agentName);

  assert.deepEqual(manifest.defaultInitPrompt, {
    zh: "基于XX文件夹的素材，写一篇“XX”主题的研究报告，篇幅X页",
    en: "Based on the materials in the XX folder, write an X-page research report on “XX”.",
  });
  assert.deepEqual(manifest.quickPrompts[0], manifest.defaultInitPrompt);

  // every packaged agent file exists and still declares no tools
  for (const relative of manifest.agents) {
    const agentPath = path.join(teamDir, relative.replace(/^\.\//u, ""));
    assert.equal(fs.existsSync(agentPath), true, `packaged ${relative}`);
    const frontmatter = fs.readFileSync(agentPath, "utf8").match(/^---\r?\n([\s\S]*?)\r?\n---/u)?.[1] ?? "";
    assert.doesNotMatch(frontmatter, /^tools:/mu, `packaged ${relative} declares tools`);
  }

  for (const registrations of Object.values(hooks.hooks) as Array<Array<{ hooks: Array<{ command: string }> }>>) {
    const command = registrations[0].hooks[0].command;
    if (process.platform === "win32") {
      assert.match(command, /^cmd\.exe \/d \/c call "\$\{CODEBUDDY_PLUGIN_ROOT\}\\bin\\run-node\.cmd" /u);
      assert.match(command, /"\$\{CODEBUDDY_PLUGIN_ROOT\}\\bin\\capture-checkpoint\.mjs"/u);
      assert.doesNotMatch(command, /\/s|""|\bsh\b/u);
    } else {
      assert.match(command, /^sh "\$\{CODEBUDDY_PLUGIN_ROOT\}\/bin\/run-node" /u);
      assert.match(command, /"\$\{CODEBUDDY_PLUGIN_ROOT\}\/bin\/capture-checkpoint\.mjs"/u);
      assert.doesNotMatch(command, /cmd\.exe|run-node\.cmd|\\bin\\/u);
    }
  }

  // Loop 由 Hook 直接启动，插件不再声明任何 MCP 依赖
  assert.equal(manifest.dependencies, undefined);
  assert.equal(fs.existsSync(path.join(teamDir, ".mcp.json")), false);
  assert.equal(fs.existsSync(path.join(teamDir, "dist/memory-server.mjs")), false);
  assert.doesNotMatch(JSON.stringify(manifest), /mcpServers/u);
  assert.doesNotMatch(JSON.stringify(manifest), /(?:sk-|ghp_|AKIA)[A-Za-z0-9]{8,}/u);

  // Hook 打包产物必须自包含：只依赖 node: 内置模块
  const bundled = fs.readFileSync(path.join(teamDir, "bin/capture-checkpoint.mjs"), "utf8");
  const imports = [...bundled.matchAll(/^import .* from "([^"]+)";$/gmu)].map((m) => m[1]);
  assert.ok(imports.length > 0);
  for (const specifier of imports) {
    assert.match(specifier, /^node:/u, `bundled hook must not import ${specifier}`);
  }
  // 启动器代码确实被打进 bundle
  assert.match(bundled, /run-python/u);
});

test("hooks are the only Loop launch path and the build gate guards it", () => {
  // WorkBuddy Team experts do load hooks.json (confirmed with the platform side),
  // and the MCP fallback was removed — so a broken hooks.json means the Loop
  // silently never starts. The build gate must refuse to ship that.
  const builder = fs.readFileSync(path.join(root, "scripts/build-team.mjs"), "utf8");

  assert.match(builder, /Missing hooks\.json/u);
  assert.match(builder, /hooks\.json is missing the \$\{event\} registration/u);
  assert.match(builder, /does not invoke capture-checkpoint\.mjs/u);
  assert.match(builder, /Hook launcher missing from package/u);
  assert.match(builder, /Hook bundle has external imports/u);
  assert.match(builder, /Report Loop runtime missing from package/u);
  assert.match(builder, /Unexpected \.mcp\.json/u);

  // The source hooks.json already registers all three events on the host side.
  const sourceHooks = JSON.parse(fs.readFileSync(path.join(root, "hooks/hooks.json"), "utf8"));
  assert.deepEqual(
    Object.keys(sourceHooks.hooks).sort(),
    ["PostToolUse", "Stop", "UserPromptSubmit"],
  );
  for (const registrations of Object.values(sourceHooks.hooks) as Array<Array<{ hooks: Array<{ command: string }> }>>) {
    assert.match(registrations[0].hooks[0].command, /capture-checkpoint\.mjs/u);
  }

  // PostToolUse is the event that detects the Job write and spawns the Runner.
  const hook = fs.readFileSync(path.join(root, "hooks/capture-checkpoint.mjs"), "utf8");
  assert.match(hook, /mode === "post-tool"/u);
  assert.match(hook, /handleReportLoopJobWrite/u);
  assert.match(hook, /mode === "run-loop-worker"/u);
});

test("every document a prompt tells an agent to read actually ships", () => {
  // 分类规则（见 README「文档分类」）：
  //   skills/*/references/  运行时契约，随包分发，Agent 读
  //   resources/*/          成员专用资源（含脚本），随包分发，Agent 读
  //   docs/                 开发与排查文档，打包时剔除，只给人读
  // prompt 里出现 docs/ 就是分类错误：那份文档在安装目录里不存在。
  const promptFiles = [
    ...fs.readdirSync(path.join(root, "agents")).map((name) => `agents/${name}`),
    "skills/research-report-loop/SKILL.md",
    ...fs.readdirSync(path.join(root, "skills/research-report-loop/references"))
      .map((name) => `skills/research-report-loop/references/${name}`),
  ];

  for (const relative of promptFiles) {
    const content = fs.readFileSync(path.join(root, relative), "utf8");
    assert.doesNotMatch(
      content,
      /`docs\//u,
      `${relative} points an agent at docs/, which is stripped from the package`,
    );
  }

  // 反过来确认：运行时契约确实随包分发
  const teamDir = path.join(root, "release", `${sourceManifest.name}-${sourceManifest.version}`);
  if (fs.existsSync(teamDir)) {
    assert.equal(fs.existsSync(path.join(teamDir, "docs")), false);
    for (const shipped of [
      "skills/research-report-loop/references/workspace-and-delivery.md",
      "skills/research-report-loop/references/loop-orchestration.md",
      "skills/research-report-loop/references/memory-orchestration.md",
      "skills/research-report-loop/references/writing-instructions.md",
      "resources/evidence/references/cleaning-rules.md",
      "resources/evidence/references/output-contract.md",
      "resources/evidence/references/source-changes.md",
      "resources/evidence/scripts/source_inventory.py",
      "resources/evidence/structured_data.schema.json",
    ]) {
      assert.equal(fs.existsSync(path.join(teamDir, shipped)), true, `missing: ${shipped}`);
    }
  }
});
