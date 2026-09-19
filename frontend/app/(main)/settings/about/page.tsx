import { APP_VERSION_LABEL } from '@/lib/appVersion';

export default function AboutSettingsPage() {
  return (
    <div
      style={{
        height: '100%',
        overflowY: 'auto',
        background: 'var(--po-canvas)',
        color: 'var(--po-text)',
        fontFamily: 'var(--po-font-sans)',
      }}
    >
      <header
        style={{
          height: 46,
          display: 'flex',
          alignItems: 'center',
          padding: '0 16px',
          borderBottom: '1px solid var(--po-divider)',
          background: 'var(--po-header)',
          fontSize: 13,
          fontWeight: 500,
        }}
      >
        About
      </header>
      <div style={{ width: 'min(560px, calc(100% - 48px))', margin: '32px auto' }}>
        <div
          style={{
            padding: 20,
            border: '1px solid var(--po-border-subtle)',
            borderRadius: 10,
            background: 'var(--po-panel)',
          }}
        >
          <div style={{ marginBottom: 4, fontSize: 14, fontWeight: 600 }}>PuppyOne Cloud</div>
          <div style={{ color: 'var(--po-text-muted)', fontSize: 12 }}>
            Version {APP_VERSION_LABEL}
          </div>
        </div>
      </div>
    </div>
  );
}
