import assert from "node:assert/strict";
import { EditorState } from "@codemirror/state";
import createJiti from "jiti";

const jiti = createJiti(import.meta.url, { interopDefault: true });
const { getMarkdownHtmlBlock } = jiti("../shared-ui/src/editor/markdown/rendering/htmlBlockModel.ts");
const { findWikiLinkTokens } = jiti("../shared-ui/src/editor/markdown/links/wikiLinkModel.ts");
const { findMarkdownLinkTokens } = jiti("../shared-ui/src/editor/markdown/links/markdownLinkModel.ts");
const { createMarkdownLinkGraph } = jiti("../shared-ui/src/editor/markdown/links/markdownLinkGraph.ts");

function parseBlock(markdown, lineNumber = 1) {
  return getMarkdownHtmlBlock(EditorState.create({ doc: markdown }), lineNumber);
}

function assertBlock(name, markdown, expected, lineNumber = 1) {
  const block = parseBlock(markdown, lineNumber);
  assert.ok(block, `${name}: expected an HTML block`);
  assert.equal(block.tagName, expected.tagName, `${name}: tagName`);
  assert.equal(block.closed, expected.closed, `${name}: closed`);
  assert.equal(block.nextLineNumber, expected.nextLineNumber, `${name}: nextLineNumber`);
  assert.equal(block.source, expected.source, `${name}: source`);
}

function assertNoBlock(name, markdown, lineNumber = 1) {
  assert.equal(parseBlock(markdown, lineNumber), null, `${name}: expected no HTML block`);
}

assertBlock(
  "container html can span blank lines",
  [
    '<div style="max-width: 1180px; margin: 0 auto;">',
    "  <section>",
    "    <p>first</p>",
    "",
    "    <p>second</p>",
    "  </section>",
    "</div>",
    "",
    "after",
  ].join("\n"),
  {
    tagName: "div",
    closed: true,
    nextLineNumber: 8,
    source: [
      '<div style="max-width: 1180px; margin: 0 auto;">',
      "  <section>",
      "    <p>first</p>",
      "",
      "    <p>second</p>",
      "  </section>",
      "</div>",
    ].join("\n"),
  },
);

assertBlock(
  "nested same-name root tags stay balanced",
  ["<div>", "  <div>inner</div>", "</div>", "tail"].join("\n"),
  {
    tagName: "div",
    closed: true,
    nextLineNumber: 4,
    source: ["<div>", "  <div>inner</div>", "</div>"].join("\n"),
  },
);

assertBlock(
  "quoted greater-than in attributes is not treated as tag end",
  ['<div data-label="a > b">', "content", "</div>", "tail"].join("\n"),
  {
    tagName: "div",
    closed: true,
    nextLineNumber: 4,
    source: ['<div data-label="a > b">', "content", "</div>"].join("\n"),
  },
);

assertBlock(
  "void tags close on the same line",
  ['<img src="cover.png" alt="Cover">', "caption"].join("\n"),
  {
    tagName: "img",
    closed: true,
    nextLineNumber: 2,
    source: '<img src="cover.png" alt="Cover">',
  },
);

assertBlock(
  "explicit self-closing custom tags close on the same line",
  ["<custom-card />", "after"].join("\n"),
  {
    tagName: "custom-card",
    closed: true,
    nextLineNumber: 2,
    source: "<custom-card />",
  },
);

assertBlock(
  "unclosed container remains unsupported",
  ["<section>", "content"].join("\n"),
  {
    tagName: "section",
    closed: false,
    nextLineNumber: 3,
    source: ["<section>", "content"].join("\n"),
  },
);

assertBlock(
  "sibling html blocks remain separate blocks",
  ["<div>one</div>", "<div>two</div>"].join("\n"),
  {
    tagName: "div",
    closed: true,
    nextLineNumber: 2,
    source: "<div>one</div>",
  },
);

assertBlock(
  "second sibling html block can be parsed independently",
  ["<div>one</div>", "<div>two</div>"].join("\n"),
  {
    tagName: "div",
    closed: true,
    nextLineNumber: 3,
    source: "<div>two</div>",
  },
  2,
);

assertNoBlock("inline html after text is not a block", "before <div>inline</div>");
assertNoBlock("html comments are not rendered as html container blocks", "<!-- comment -->");
assertNoBlock("closing tags do not start blocks", "</div>");

const wikiTokens = findWikiLinkTokens("See [[Notes/Project#Plan|project plan]] and \\[[literal]].");
assert.equal(wikiTokens.length, 1, "wiki links: escaped links are ignored");
assert.equal(wikiTokens[0].target, "Notes/Project#Plan", "wiki links: target");
assert.equal(wikiTokens[0].targetPath, "Notes/Project", "wiki links: target path");
assert.equal(wikiTokens[0].heading, "Plan", "wiki links: heading");
assert.equal(wikiTokens[0].label, "project plan", "wiki links: alias");

const markdownLinkTokens = findMarkdownLinkTokens("See [project plan](Notes/Project.md#Plan) and ![image](cover.png).");
assert.equal(markdownLinkTokens.length, 1, "markdown links: images are ignored");
assert.equal(markdownLinkTokens[0].label, "project plan", "markdown links: label");
assert.equal(markdownLinkTokens[0].href, "Notes/Project.md#Plan", "markdown links: href");

const graph = createMarkdownLinkGraph([
  {
    path: "Notes/Project.md",
    name: "Project.md",
    content: "Project body",
  },
  {
    path: "Attachments/Reference Guide.pdf",
    name: "Reference Guide.pdf",
    content: null,
  },
  {
    path: "Inbox.md",
    name: "Inbox.md",
    content: "See [[Project]], [[Notes/Project#Plan|the plan]], [standard](Notes/Project.md), and [PDF](Attachments/Reference Guide.pdf).",
  },
  {
    path: "Notes/Nested.md",
    name: "Nested.md",
    content: "See [relative](../Attachments/Reference Guide.pdf) and [[Reference Guide]].",
  },
]);
const resolvedTitle = graph.resolveWikiLink("Inbox.md", "Project");
assert.equal(resolvedTitle.exists, true, "wiki graph: resolves by title");
assert.equal(resolvedTitle.path, "Notes/Project.md", "wiki graph: resolved path");
const resolvedMarkdownLink = graph.resolveMarkdownLink("Inbox.md", "Notes/Project.md");
assert.equal(resolvedMarkdownLink?.exists, true, "wiki graph: resolves markdown links");
assert.equal(resolvedMarkdownLink?.path, "Notes/Project.md", "wiki graph: resolved markdown link path");
const resolvedPdf = graph.resolveMarkdownLink("Inbox.md", "Attachments/Reference Guide.pdf");
assert.equal(resolvedPdf?.exists, true, "wiki graph: resolves file attachments");
assert.equal(resolvedPdf?.path, "Attachments/Reference Guide.pdf", "wiki graph: resolved file attachment path");
const resolvedRelativePdf = graph.resolveMarkdownLink("Notes/Nested.md", "../Attachments/Reference Guide.pdf");
assert.equal(resolvedRelativePdf?.exists, true, "wiki graph: resolves relative file attachments");
assert.equal(resolvedRelativePdf?.path, "Attachments/Reference Guide.pdf", "wiki graph: resolved relative file attachment path");
const resolvedWikiPdf = graph.resolveWikiLink("Notes/Nested.md", "Reference Guide");
assert.equal(resolvedWikiPdf.exists, true, "wiki graph: resolves attachment wiki links by title");
assert.equal(resolvedWikiPdf.path, "Attachments/Reference Guide.pdf", "wiki graph: resolved attachment wiki link path");
const unresolvedRootLink = graph.resolveWikiLink("Notes/Nested.md", "KOL/KOL-合作策略");
assert.equal(unresolvedRootLink.exists, false, "wiki graph: unresolved root-ish path remains missing");
assert.deepEqual(
  unresolvedRootLink.candidatePaths?.slice(0, 6),
  [
    "Notes/KOL/KOL-合作策略",
    "Notes/KOL/KOL-合作策略.md",
    "Notes/KOL/KOL-合作策略.markdown",
    "KOL/KOL-合作策略",
    "KOL/KOL-合作策略.md",
    "KOL/KOL-合作策略.markdown",
  ],
  "wiki graph: unresolved links expose navigation candidates",
);
const unresolvedMarkdownLink = graph.resolveMarkdownLink("Notes/Nested.md", "../Playbook/kol-list");
assert.equal(unresolvedMarkdownLink?.exists, false, "wiki graph: unresolved markdown links remain missing");
assert.deepEqual(
  unresolvedMarkdownLink?.candidatePaths?.slice(0, 3),
  ["Playbook/kol-list", "Playbook/kol-list.md", "Playbook/kol-list.markdown"],
  "wiki graph: unresolved markdown links expose decoded navigation candidates",
);
const backlinks = graph.getBacklinks("Notes/Project.md");
assert.equal(backlinks.length, 1, "wiki graph: backlink source count");
assert.equal(backlinks[0].count, 3, "wiki graph: backlink reference count");
assert.equal(backlinks[0].references[0].lineNumber, 1, "wiki graph: backlink line number");
const pdfBacklinks = graph.getBacklinks("Attachments/Reference Guide.pdf");
assert.equal(pdfBacklinks.length, 2, "wiki graph: attachment backlink source count");

console.log("Markdown HTML and wiki link parser fixtures passed.");
