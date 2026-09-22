/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'selector',
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    // Domain components live here after extraction from app route entries.
    './features/**/*.{js,ts,jsx,tsx,mdx}',
    './lib/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['var(--po-font-sans)'],
        mono: ['var(--po-font-mono)'],
      },
      fontSize: {
        'po-micro': ['var(--po-text-size-micro)', { lineHeight: 'var(--po-line-height-tight)' }],
        'po-caption': ['var(--po-text-size-caption)', { lineHeight: 'var(--po-line-height-tight)' }],
        'po-meta': ['var(--po-text-size-meta)', { lineHeight: 'var(--po-line-height-body)' }],
        'po-body': ['var(--po-text-size-body)', { lineHeight: 'var(--po-line-height-body)' }],
        'po-body-lg': ['var(--po-text-size-body-lg)', { lineHeight: 'var(--po-line-height-body)' }],
        'po-title': ['var(--po-text-size-title)', { lineHeight: 'var(--po-line-height-tight)' }],
        'po-page-title': ['var(--po-text-size-page-title)', { lineHeight: 'var(--po-line-height-tight)' }],
        'po-display': ['var(--po-text-size-display)', { lineHeight: 'var(--po-line-height-tight)' }],
      },
      colors: {
        po: {
          canvas: 'var(--po-canvas)',
          sidebar: 'var(--po-sidebar)',
          header: 'var(--po-header)',
          panel: 'var(--po-panel)',
          'panel-raised': 'var(--po-panel-raised)',
          overlay: 'var(--po-overlay)',
          inset: 'var(--po-inset)',
          text: 'var(--po-text)',
          muted: 'var(--po-text-muted)',
          subtle: 'var(--po-text-subtle)',
          disabled: 'var(--po-text-disabled)',
          border: 'var(--po-border)',
          accent: 'var(--po-accent)',
          success: 'var(--po-success)',
          warning: 'var(--po-warning)',
          danger: 'var(--po-danger)',
          info: 'var(--po-info)',
        },
      },
    },
  },
  plugins: [],
};






