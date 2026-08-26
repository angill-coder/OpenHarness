import fs from "node:fs";
import path from "node:path";

const [configDir, pluginRef, installPath, version, marketplaceName, marketplaceRoot] = process.argv.slice(2);
if (![configDir, pluginRef, installPath, version, marketplaceName, marketplaceRoot].every(Boolean)) {
  throw new Error(
    "usage: register-workbuddy-local.mjs <configDir> <pluginRef> <installPath> "
      + "<version> <marketplaceName> <marketplaceRoot>",
  );
}

function readJson(file, fallback) {
  if (!fs.existsSync(file)) return fallback;
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function writeJsonAtomic(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.research-report-loop-memory.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, file);
}

const now = new Date().toISOString();
const pluginName = pluginRef.split("@", 1)[0];
const cacheInstallPath = path.join(
  configDir,
  "plugins/cache",
  marketplaceName,
  pluginName,
  version,
);
const sourcePath = path.resolve(installPath);
const registeredInstallPath = path.resolve(cacheInstallPath);
if (sourcePath !== registeredInstallPath) {
  fs.mkdirSync(registeredInstallPath, { recursive: true });
  fs.cpSync(sourcePath, registeredInstallPath, { recursive: true, force: true });
}

const installedFile = path.join(configDir, "plugins/installed_plugins.json");
const installed = readJson(installedFile, { version: 2, plugins: {} });
installed.version ??= 2;
installed.plugins ??= {};
const prior = Array.isArray(installed.plugins[pluginRef]) ? installed.plugins[pluginRef][0] : undefined;
installed.plugins[pluginRef] = [{
  scope: "user",
  installPath: registeredInstallPath,
  version,
  installedAt: prior?.installedAt ?? now,
  lastUpdated: now,
}];
writeJsonAtomic(installedFile, installed);

const settingsFile = path.join(configDir, "settings.json");
const settings = readJson(settingsFile, {});
settings.enabledPlugins ??= {};
settings.enabledPlugins[pluginRef] = true;
writeJsonAtomic(settingsFile, settings);

const mcpFile = path.join(configDir, ".mcp.json");
const mcp = readJson(mcpFile, { mcpServers: {} });
mcp.mcpServers ??= {};
delete mcp.mcpServers["research-report-loop"];
delete mcp.mcpServers["openharness-report-loop"];
delete mcp.mcpServers["local-report-loop"];
const nodeLauncher = path.join(registeredInstallPath, "scripts", process.platform === "win32" ? "run-node.cmd" : "run-node.sh");
const serverEntry = path.join(registeredInstallPath, "dist", "memory-server.mjs");
const baseRubric = path.join(registeredInstallPath, "rubrics", "v2_rubric_research.json");
mcp.mcpServers["report-memory-v2"] = process.platform === "win32" ? {
  command: "cmd.exe",
  args: ["/d", "/c", nodeLauncher, serverEntry],
  env: {
    RESEARCH_REPORT_MEMORY_V2_0821_DIR: "~/.research-report-memory-v2-0821",
    RESEARCH_REPORT_BASE_RUBRIC_PATH: baseRubric,
  },
} : {
  command: "sh",
  args: [nodeLauncher, serverEntry],
  env: {
    RESEARCH_REPORT_MEMORY_V2_0821_DIR: "~/.research-report-memory-v2-0821",
    RESEARCH_REPORT_BASE_RUBRIC_PATH: baseRubric,
  },
};
writeJsonAtomic(mcpFile, mcp);

const marketplaceManifestFile = path.join(marketplaceRoot, ".codebuddy-plugin/marketplace.json");
const marketplaceManifest = readJson(marketplaceManifestFile, null);
if (!marketplaceManifest) {
  throw new Error(`missing marketplace manifest: ${marketplaceManifestFile}`);
}
const marketplacesFile = path.join(configDir, "plugins/known_marketplaces.json");
const marketplaces = readJson(marketplacesFile, {});
marketplaces[marketplaceName] = {
  type: "directory",
  source: { source: "directory", path: marketplaceRoot },
  installLocation: marketplaceRoot,
  description: `Marketplace from ${marketplaceRoot}`,
  lastUpdated: now,
  autoUpdate: false,
  manifest: marketplaceManifest,
};
writeJsonAtomic(marketplacesFile, marketplaces);

process.stdout.write(`${JSON.stringify({
  status: "registered",
  pluginRef,
  installPath: registeredInstallPath,
  sourcePath,
  version,
  mcpServer: "report-memory-v2",
})}\n`);
