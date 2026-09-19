'use client';

export function ProjectUnavailableShell({
  onBackHome,
}: {
  onBackHome: () => void;
}) {
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        background: 'var(--po-canvas)',
      }}
    >
      <div
        style={{
          height: 46,
          minHeight: 46,
          flexShrink: 0,
          borderBottom: '1px solid var(--po-divider)',
          background: 'var(--po-canvas)',
        }}
      />
      <div
        style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 24,
        }}
      >
        <div
          style={{
            width: 'min(360px, 100%)',
            textAlign: 'center',
            color: 'var(--po-text-muted)',
            fontFamily: 'var(--po-font-sans)',
          }}
        >
          <div
            style={{
              fontSize: 14,
              fontWeight: 600,
              color: 'var(--po-text)',
              marginBottom: 6,
            }}
          >
            Project unavailable
          </div>
          <div style={{ fontSize: 13, lineHeight: 1.55, marginBottom: 16 }}>
            This project may have been deleted, moved, or you may not have access.
          </div>
          <button
            type="button"
            onClick={onBackHome}
            style={{
              height: 32,
              padding: '0 14px',
              borderRadius: 6,
              border: '1px solid var(--po-border-strong)',
              background: 'transparent',
              color: 'var(--po-text)',
              fontSize: 13,
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            Back to Home
          </button>
        </div>
      </div>
    </div>
  );
}
