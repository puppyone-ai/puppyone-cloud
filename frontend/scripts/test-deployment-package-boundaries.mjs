import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const appPackage = readJson(path.join(frontendRoot, 'package.json'));
const lockfile = readJson(path.join(frontendRoot, 'package-lock.json'));
const dependency = appPackage.dependencies?.['@puppyone/cloud-core'];

assert(
  dependency === 'file:packages/cloud-core',
  '@puppyone/cloud-core must be a frontend-local file dependency',
);

const packageRoot = path.resolve(frontendRoot, dependency.slice('file:'.length));
const relativePackageRoot = path.relative(frontendRoot, packageRoot);
assert(
  relativePackageRoot !== '' &&
    relativePackageRoot !== '..' &&
    !relativePackageRoot.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relativePackageRoot),
  '@puppyone/cloud-core must stay inside the frontend deployment context',
);

const cloudPackagePath = path.join(packageRoot, 'package.json');
assert(existsSync(cloudPackagePath), 'frontend-local cloud-core package is missing');

const cloudPackage = readJson(cloudPackagePath);
assert(cloudPackage.name === '@puppyone/cloud-core', 'cloud-core package name is invalid');
for (const exportTarget of Object.values(cloudPackage.exports ?? {})) {
  assert(
    typeof exportTarget === 'string' && existsSync(path.resolve(packageRoot, exportTarget)),
    `cloud-core export target is missing: ${String(exportTarget)}`,
  );
}

assert(
  lockfile.packages?.['']?.dependencies?.['@puppyone/cloud-core'] === dependency,
  'package-lock.json root dependency is out of sync',
);
assert(
  lockfile.packages?.['node_modules/@puppyone/cloud-core']?.resolved === 'packages/cloud-core',
  'package-lock.json must resolve cloud-core inside frontend',
);

console.log('frontend deployment package boundary contract passed.');

function readJson(filePath) {
  return JSON.parse(readFileSync(filePath, 'utf8'));
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
