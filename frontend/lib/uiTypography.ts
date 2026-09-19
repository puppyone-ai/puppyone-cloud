import type { CSSProperties } from 'react';

export const FONT_SANS = 'var(--po-font-sans)';
export const FONT_MONO = 'var(--po-font-mono)';

export const TEXT_SIZE = {
  micro: 'var(--po-text-size-micro)',
  caption: 'var(--po-text-size-caption)',
  meta: 'var(--po-text-size-meta)',
  body: 'var(--po-text-size-body)',
  bodyLg: 'var(--po-text-size-body-lg)',
  title: 'var(--po-text-size-title)',
  pageTitle: 'var(--po-text-size-page-title)',
  display: 'var(--po-text-size-display)',
} as const;

export const TEXT_WEIGHT = {
  regular: 'var(--po-text-weight-regular)',
  medium: 'var(--po-text-weight-medium)',
  semibold: 'var(--po-text-weight-semibold)',
  bold: 'var(--po-text-weight-bold)',
} as const;

export const LINE_HEIGHT = {
  tight: 'var(--po-line-height-tight)',
  body: 'var(--po-line-height-body)',
  relaxed: 'var(--po-line-height-relaxed)',
} as const;

export const TYPOGRAPHY = {
  body: {
    fontFamily: FONT_SANS,
    fontSize: TEXT_SIZE.body,
    fontWeight: TEXT_WEIGHT.regular,
    lineHeight: LINE_HEIGHT.body,
    letterSpacing: 0,
  },
  label: {
    fontFamily: FONT_SANS,
    fontSize: TEXT_SIZE.body,
    fontWeight: TEXT_WEIGHT.medium,
    lineHeight: LINE_HEIGHT.tight,
    letterSpacing: 0,
  },
  meta: {
    fontFamily: FONT_SANS,
    fontSize: TEXT_SIZE.meta,
    fontWeight: TEXT_WEIGHT.medium,
    lineHeight: LINE_HEIGHT.body,
    letterSpacing: 0,
  },
  title: {
    fontFamily: FONT_SANS,
    fontSize: TEXT_SIZE.title,
    fontWeight: TEXT_WEIGHT.semibold,
    lineHeight: LINE_HEIGHT.tight,
    letterSpacing: 0,
  },
  pageTitle: {
    fontFamily: FONT_SANS,
    fontSize: TEXT_SIZE.pageTitle,
    fontWeight: TEXT_WEIGHT.semibold,
    lineHeight: LINE_HEIGHT.tight,
    letterSpacing: 0,
  },
  chromeLabel: {
    fontFamily: FONT_SANS,
    fontSize: 'var(--po-font-size-chrome)',
    fontWeight: 'var(--po-font-weight-chrome)',
    lineHeight: LINE_HEIGHT.tight,
    letterSpacing: 0,
  },
  monoBody: {
    fontFamily: FONT_MONO,
    fontSize: TEXT_SIZE.body,
    fontWeight: TEXT_WEIGHT.regular,
    lineHeight: LINE_HEIGHT.body,
    letterSpacing: 0,
  },
  monoMeta: {
    fontFamily: FONT_MONO,
    fontSize: TEXT_SIZE.meta,
    fontWeight: TEXT_WEIGHT.medium,
    lineHeight: LINE_HEIGHT.body,
    letterSpacing: 0,
  },
} satisfies Record<string, CSSProperties>;

export const CHROME_LABEL_TYPOGRAPHY: CSSProperties = {
  ...TYPOGRAPHY.chromeLabel,
};

export const SIDEBAR_ROW_TYPOGRAPHY: CSSProperties = {
  ...TYPOGRAPHY.body,
  fontSize: '14px',
  fontWeight: TEXT_WEIGHT.regular,
};

export const SIDEBAR_META_TYPOGRAPHY: CSSProperties = {
  fontFamily: FONT_SANS,
  fontSize: TEXT_SIZE.meta,
  fontWeight: TEXT_WEIGHT.medium,
  lineHeight: LINE_HEIGHT.body,
  letterSpacing: 0,
};
