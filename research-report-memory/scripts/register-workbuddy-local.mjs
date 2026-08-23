import fs from "node:fs";
import path from "node:path";

const [configDir, pluginRef, installPath, version, marketplaceName, marketplaceRoot] = process.argv.slice(2);
if (![configDir, pluginRef, installPath, version, marketplaceName, marketplaceRoot].every(Boolean)) {
  throw new Error("usage: register-workbuddy-local.mjs <configDir> <pluginRef> <installPath> <version> <marketplaceName> <marketplaceRoot>");
}

function readJson(file, fallback) {
  if (!fs.existsSync(file)) return fallback;
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function writeJsonAtomic(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temporary = `${file}.research-report-memory.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, file);
}

const now = new Date().toISOString();
const installedFile = path.join(configDir, "plugins/installed_plugins.json");
const installed = readJson(installedFile, { version: 2, plugins: {} });
installed.version ??= 2;
installed.plugins ??= {};
const prior = Array.isArray(installed.plugins[pluginRef]) ? installed.plugins[pluginRef][0] : undefined;
installed.plugins[pluginRef] = [{
  scope: "user",
  installPath,
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

const marketplaceManifestFile = path.join(marketplaceRoot, ".codebuddy-plugin/marketplace.json");
const marketplaceManifest = readJson(marketplaceManifestFile, null);
if (!marketplaceManifest) throw new Error(`missing marketplace manifest: ${marketplaceManifestFile}`);
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

const mcpFile = path.join(configDir, ".mcp.json");
const mcp = readJson(mcpFile, { mcpServers: {} });
mcp.mcpServers ??= {};
mcp.mcpServers["research-report-memory-v2-mvp"] = {
  args: [
    path.join(installPath, "scripts/run-node.sh"),
    path.join(installPath, "dist/server.mjs"),
  ],
  print: true,
  command: "sh",
  type: "stdio",
};
writeJsonAtomic(mcpFile, mcp);

console.log(JSON.stringify({ status: "registered", pluginRef, installPath, version }));
