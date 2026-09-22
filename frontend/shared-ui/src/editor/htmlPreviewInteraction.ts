export function getHtmlPreviewInteractionCss(rootSelector: string): string {
  return `${rootSelector},
${rootSelector} * {
  -webkit-user-select: none !important;
  user-select: none !important;
}

${rootSelector} :where(
  input:not([type="button"]):not([type="checkbox"]):not([type="radio"]):not([type="reset"]):not([type="submit"]),
  textarea,
  [contenteditable=""],
  [contenteditable="true"],
  pre,
  code,
  kbd,
  samp
) {
  -webkit-user-select: text !important;
  user-select: text !important;
}

${rootSelector} :where(
  button,
  input[type="button"],
  input[type="checkbox"],
  input[type="radio"],
  input[type="reset"],
  input[type="submit"],
  a,
  summary
) {
  -webkit-user-select: none !important;
  user-select: none !important;
}`;
}
