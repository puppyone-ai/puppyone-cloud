// @vitest-environment node
import { createRequire } from 'node:module';
import postcss, { type Rule } from 'postcss';
import tailwindcss from 'tailwindcss';
import { expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const config = require('../../tailwind.config.cjs');

it('builds the empty workspace layout from its feature source with the production Tailwind configuration', async () => {
  // Compile the real source scan: DOM tests cannot detect missing generated CSS
  // when a component moves out of a directory listed in Tailwind's content.
  const result = await postcss([tailwindcss(config)]).process('@tailwind utilities;', {
    from: 'app/globals.css',
  });
  const rules = new Map<string, Rule>();
  result.root.walkRules(rule => { rules.set(rule.selector, rule); });
  const declaration = (selector: string, property: string) => {
    let value: string | undefined;
    rules.get(selector)?.walkDecls(property, decl => { value = decl.value; });
    return value;
  };

  // Desktop has two cards side by side; the existing mobile layout stays one
  // column because the two-column rule only applies from the md breakpoint.
  const columns = rules.get('.md\\:grid-cols-2');
  expect(columns, 'desktop card columns must be emitted').toBeDefined();
  expect(columns?.parent).toMatchObject({ type: 'atrule', name: 'media', params: '(min-width: 768px)' });
  expect(declaration('.md\\:grid-cols-2', 'grid-template-columns')).toBe('repeat(2, minmax(0, 1fr))');
  expect(declaration('.min-h-full', 'min-height')).toBe('100%');
  expect(declaration('.py-14', 'padding-top')).toBe('3.5rem');
  expect(declaration('.py-14', 'padding-bottom')).toBe('3.5rem');
  expect(declaration('.min-h-\\[188px\\]', 'min-height')).toBe('188px');
});
