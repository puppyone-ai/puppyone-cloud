import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const ROOT = process.cwd();
const SCAN_DIRS = ['app', 'components', 'lib'];
const SOURCE_EXT = new Set(['.ts', '.tsx', '.css']);

const RAW_COLOUR =
  /#[0-9a-fA-F]{3,8}\b|rgba\(|(?:background|color|border(?:Color)?):\s*['"](?:black|white)['"]|\b(?:fill|stroke)=['"](?:black|white)['"]|bg-\[#|text-\[#|border-\[#/;
const TAILWIND_NAMED_COLOUR =
  /\b(?:bg|text|border|ring|from|to|via|shadow|fill|stroke)-(?:black|white|slate|gray|zinc|neutral|stone|red|orange|amber|yellow|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)(?:-(?:50|100|200|300|400|500|600|700|800|900|950))?(?:\/\d+)?\b/;
const TOKEN_ALPHA_SUFFIX =
  /\$\{[^}]+\}(?:[a-fA-F0-9]{2})\b|var\(--po-[^)]+\)[a-fA-F0-9]{2}\b/;
const DISALLOWED = new RegExp(
  `${RAW_COLOUR.source}|${TAILWIND_NAMED_COLOUR.source}|${TOKEN_ALPHA_SUFFIX.source}`
);

// Explicit brand/icon colours. Product surfaces should not be added here.
const ALLOWED_BRAND = [
  '#4285f4',
  '#34a853',
  '#fbbc04',
  '#ea4335',
  '#3ECF8E',
  '#249361',
  '#06130c',
  '#4599DF',
];

const ALLOWED_PATH_PARTS = ['app/dev/', 'lib/theme/monacoThemes.ts'];

function isSource(path) {
  return SOURCE_EXT.has(path.slice(path.lastIndexOf('.')));
}

function walk(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    const rel = relative(ROOT, path);
    if (rel.includes('node_modules') || rel.includes('.next')) continue;
    const info = statSync(path);
    if (info.isDirectory()) {
      walk(path, files);
    } else if (isSource(path)) {
      files.push(path);
    }
  }
  return files;
}

function isCommentOnly(line) {
  const trimmed = line.trim();
  return (
    trimmed.startsWith('//') ||
    trimmed.startsWith('*') ||
    trimmed.startsWith('/*') ||
    trimmed.startsWith('{/*') ||
    trimmed.startsWith('<!--')
  );
}

function hasOnlyAllowedBrand(line) {
  const withoutAllowed = ALLOWED_BRAND.reduce(
    (next, color) => next.replaceAll(color, ''),
    line
  );
  return (
    !RAW_COLOUR.test(withoutAllowed) &&
    !TAILWIND_NAMED_COLOUR.test(line) &&
    !TOKEN_ALPHA_SUFFIX.test(line)
  );
}

const failures = [];

for (const dir of SCAN_DIRS) {
  const abs = join(ROOT, dir);
  for (const file of walk(abs)) {
    const rel = relative(ROOT, file).replaceAll('\\', '/');
    if (ALLOWED_PATH_PARTS.some(part => rel.includes(part))) continue;
    if (rel === 'app/globals.css') continue;

    const lines = readFileSync(file, 'utf8').split('\n');
    lines.forEach((line, index) => {
      if (!DISALLOWED.test(line)) return;
      if (isCommentOnly(line)) return;
      if (line.includes('theme-allow-brand')) return;
      if (hasOnlyAllowedBrand(line)) return;
      failures.push(`${rel}:${index + 1}: ${line.trim()}`);
    });
  }
}

const tokenSource = readFileSync(
  join(ROOT, 'shared-ui/src/styles/tokens.css'),
  'utf8'
);

// Pixel anchors shared with Desktop's default-neutral theme. Keep these as
// final shell values: remixing header/sidebar from the warmer chrome primitive
// makes Cloud visibly yellow and breaks cross-product parity.
const REQUIRED_DESKTOP_ANCHORS = [
  '--po-surface-panel: #fafafa;',
  '--po-surface-panel: #1a1a1a;',
  '--po-surface-panel-raised: #ffffff;',
  '--po-surface-panel-raised: #222222;',
  '--po-header: #ebebeb;',
  '--po-sidebar: #ebebeb;',
  '--po-surface-chrome: #202020;',
];

for (const anchor of REQUIRED_DESKTOP_ANCHORS) {
  if (!tokenSource.includes(anchor)) {
    failures.push(
      `shared-ui/src/styles/tokens.css: missing Desktop theme anchor ${anchor}`
    );
  }
}

if (/--po-(?:header|sidebar)\s*:\s*color-mix\(/.test(tokenSource)) {
  failures.push(
    'shared-ui/src/styles/tokens.css: header/sidebar must use Desktop final neutral values, not color-mix()'
  );
}

const definedTokens = new Set(
  [...tokenSource.matchAll(/--po-[a-z0-9-]+\s*:/g)].map(match =>
    match[0].slice(0, -1).trim()
  )
);
const usedTokens = new Map();

for (const dir of SCAN_DIRS) {
  const abs = join(ROOT, dir);
  for (const file of walk(abs)) {
    const rel = relative(ROOT, file).replaceAll('\\', '/');
    const content = readFileSync(file, 'utf8');
    for (const match of content.matchAll(/var\((--po-[^)\s,]+)\)/g)) {
      const token = match[1];
      if (!usedTokens.has(token)) usedTokens.set(token, new Set());
      usedTokens.get(token).add(rel);
    }
  }
}

for (const [token, files] of usedTokens) {
  if (definedTokens.has(token)) continue;
  failures.push(`${[...files][0]}: undefined theme token ${token}`);
}

if (failures.length > 0) {
  console.error('Found non-tokenized theme colours:');
  for (const failure of failures) console.error(`  ${failure}`);
  process.exit(1);
}

console.log('Theme colour audit passed.');
