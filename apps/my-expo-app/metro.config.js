// Learn more https://docs.expo.dev/guides/monorepos/#configure-metro
const { getDefaultConfig } = require('expo/metro-config');
const path = require('path');

// Find the project and workspace directories
const projectRoot = __dirname;
// This can be replaced with `find-yarn-workspace-root`
const workspaceRoot = path.resolve(projectRoot, '../..');

console.log(`[Metro Config] Project Root: ${projectRoot}`);
console.log(`[Metro Config] Workspace Root: ${workspaceRoot}`);

const config = getDefaultConfig(projectRoot);

// 1. Watch all files within the monorepo
config.watchFolders = [workspaceRoot];
console.log(`[Metro Config] Watch Folders: ${JSON.stringify(config.watchFolders)}`);

// 2. Let Metro know where to resolve packages and in what order
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, 'node_modules'),
  path.resolve(workspaceRoot, 'node_modules'),
];
console.log(`[Metro Config] Node Modules Paths: ${JSON.stringify(config.resolver.nodeModulesPaths)}`);

// config.resolver.disableHierarchicalLookup = true; // Emilio: This was causing issues with the build.

console.log(`[Metro Config] Final Metro config being exported.`);

module.exports = config; 