#!/usr/bin/env bash
#
# Reads version from root version.json and syncs it to all sub-projects.
# Usage: ./scripts/sync-version.sh
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=$(node -e "console.log(require('$ROOT_DIR/version.json').version)")

echo "Syncing version: $VERSION"

# 1) frontend/package.json
node -e "
  const fs = require('fs');
  const p = '$ROOT_DIR/frontend/package.json';
  const pkg = JSON.parse(fs.readFileSync(p, 'utf-8'));
  pkg.version = '$VERSION';
  fs.writeFileSync(p, JSON.stringify(pkg, null, 2) + '\n');
"
echo "  ✓ frontend/package.json"

# 2) frontend/package-lock.json
node -e "
  const fs = require('fs');
  const p = '$ROOT_DIR/frontend/package-lock.json';
  if (fs.existsSync(p)) {
    const lock = JSON.parse(fs.readFileSync(p, 'utf-8'));
    lock.version = '$VERSION';
    if (lock.packages && lock.packages['']) {
      lock.packages[''].version = '$VERSION';
    }
    fs.writeFileSync(p, JSON.stringify(lock, null, 2) + '\n');
  }
"
echo "  ✓ frontend/package-lock.json"

# The puppyone CLI is released from its own repository
# (puppyone-ai/puppyone-cli) on an independent npm version line. It is
# intentionally not synced here.

# 3) backend/pyproject.toml
sed -i '' "s/^version = \".*\"/version = \"$VERSION\"/" "$ROOT_DIR/backend/pyproject.toml"
echo "  ✓ backend/pyproject.toml"

# 4) backend/src/config.py
sed -i '' "s/VERSION: str = \".*\"/VERSION: str = \"$VERSION\"/" "$ROOT_DIR/backend/src/config.py"
echo "  ✓ backend/src/config.py"

# 5) backend/src/__init__.py
sed -i '' "s/__version__ = \".*\"/__version__ = \"$VERSION\"/" "$ROOT_DIR/backend/src/__init__.py"
echo "  ✓ backend/src/__init__.py"

# 6) backend/uv.lock
sed -i '' "/^name = \"contextbase\"$/,/^\\[\\[package\\]\\]/ s/^version = \".*\"/version = \"$VERSION\"/" "$ROOT_DIR/backend/uv.lock"
echo "  ✓ backend/uv.lock"

echo ""
echo "All synced to v$VERSION"
