'use client';

import { useLayoutEffect, useRef, type ReactNode } from 'react';

interface HistoryDetailViewportProps {
  readonly activeKey: string;
  readonly children: ReactNode;
}

export function HistoryDetailViewport({ activeKey, children }: HistoryDetailViewportProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTop = 0;
    element.scrollLeft = 0;
  }, [activeKey]);

  return (
    <div
      ref={scrollRef}
      className="history-detail-scroll min-h-0 flex-1 min-w-0 custom-scrollbar bg-[var(--po-canvas)]"
    >
      <div key={activeKey} className="history-detail-surface">
        {children}
      </div>

      <style jsx>{`
        .history-detail-scroll {
          overflow-y: scroll;
          overflow-x: hidden;
          scrollbar-gutter: stable;
          overflow-anchor: none;
        }

        .history-detail-surface {
          min-height: 100%;
          min-width: 0;
          width: 100%;
          display: flex;
          flex-direction: column;
          align-items: stretch;
          overflow-anchor: none;
          animation: history-detail-enter 120ms ease-out;
        }

        @keyframes history-detail-enter {
          from {
            opacity: 0.82;
          }
          to {
            opacity: 1;
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .history-detail-surface {
            animation: none;
          }
        }
      `}</style>
    </div>
  );
}
