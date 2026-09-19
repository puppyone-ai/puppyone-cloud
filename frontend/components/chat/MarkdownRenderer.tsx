import { CSSProperties, memo, useMemo, useRef, ReactNode } from 'react';
import ReactMarkdown, { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';

// @path 高亮样式
const PATH_MENTION_STYLE: CSSProperties = {
  background: 'var(--po-selected)',
  color: 'var(--po-accent)',
  padding: '1px 5px',
  borderRadius: 4,
  fontFamily: 'var(--po-font-sans)',
  fontSize: '0.9em',
  cursor: 'pointer',
  transition: 'background 0.15s',
};

// 解析文本中的 @path
function parseTextWithPathMentions(text: string): ReactNode[] {
  const pathRegex =
    /@([a-zA-Z_][a-zA-Z0-9_]*(?:(?:\.[a-zA-Z_][a-zA-Z0-9_]*)|(?:\[\d+\]))*)/g;
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match;
  let keyIndex = 0;

  while ((match = pathRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const path = match[1];
    parts.push(
      <span
        key={`path-${keyIndex++}`}
        style={PATH_MENTION_STYLE}
        title={`JSON Path: ${path}`}
        onMouseEnter={e => {
          e.currentTarget.style.background =
            'color-mix(in srgb, var(--po-accent) 24%, transparent)';
        }}
        onMouseLeave={e => {
          e.currentTarget.style.background = 'var(--po-selected)';
        }}
      >
        @{path}
      </span>
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [text];
}

// Define default styles as a constant to avoid recreation
const DEFAULT_STYLES: Record<string, CSSProperties> = {
  p: {
    margin: '8px 0',
    lineHeight: '1.5',
    fontSize: '14px',
    wordBreak: 'break-word',
    overflowWrap: 'break-word',
  },
  h1: {
    fontSize: '20px',
    fontWeight: 700,
    lineHeight: '1.6',
    margin: '24px 0 12px 0',
  },
  h2: {
    fontSize: '16px',
    fontWeight: 700,
    lineHeight: '1.6',
    margin: '20px 0 10px 0',
  },
  h3: {
    fontSize: '14px',
    fontWeight: 600,
    lineHeight: '1.6',
    margin: '16px 0 8px 0',
  },
  ul: { margin: '8px 0', paddingLeft: '20px', fontSize: '14px', listStyleType: 'disc' },
  ol: { margin: '8px 0', paddingLeft: '20px', fontSize: '14px', listStyleType: 'decimal' },
  li: { margin: '4px 0' },
  link: {
    color: 'var(--po-accent)',
    textDecoration: 'underline',
    textDecorationColor: 'var(--po-accent)',
    transition: 'all 0.2s ease',
    cursor: 'pointer',
    fontWeight: 500,
    wordBreak: 'break-word',
    overflowWrap: 'break-word',
    display: 'inline',
    maxWidth: '100%',
    fontSize: 'inherit',
  },
  table: {
    borderCollapse: 'separate',
    borderSpacing: 0,
    width: '100%',
    margin: '12px 0',
    fontSize: '12px',
    border: '1px solid var(--po-border)',
    backgroundColor: 'transparent',
    borderRadius: '6px',
    overflow: 'hidden',
  },
  thead: { backgroundColor: 'transparent' },
  tr: {},
  th: {
    padding: '8px 10px',
    textAlign: 'left',
    borderBottom: '1px solid var(--po-border-strong)',
    borderRight: '1px solid var(--po-border-strong)',
    fontWeight: 500,
    color: 'var(--po-text)',
    backgroundColor: 'var(--po-control)',
  },
  td: {
    padding: '6px 10px',
    borderBottom: '1px solid var(--po-border-subtle)',
    borderRight: '1px solid var(--po-border-subtle)',
    color: 'var(--po-text-muted)',
    verticalAlign: 'top',
  },
};

export interface MarkdownRendererProps {
  content: string;
  componentsStyle?: Record<string, CSSProperties>;
  onLinkEnter?: (href: string, e: React.MouseEvent) => void;
  onLinkLeave?: (e: React.MouseEvent) => void;
  onLinkMove?: (href: string, e: React.MouseEvent) => void;
}

function MarkdownRenderer({
  content,
  componentsStyle,
  onLinkEnter,
  onLinkLeave,
  onLinkMove,
}: MarkdownRendererProps) {
  // Use refs to keep handlers and styles fresh without re-rendering components
  const handlersRef = useRef({ onLinkEnter, onLinkLeave, onLinkMove });
  handlersRef.current = { onLinkEnter, onLinkLeave, onLinkMove };

  const stylesRef = useRef(componentsStyle || DEFAULT_STYLES);
  stylesRef.current = componentsStyle || DEFAULT_STYLES;

  // 处理 children，解析其中的 @path
  const processChildren = (children: ReactNode): ReactNode => {
    if (typeof children === 'string') {
      const parsed = parseTextWithPathMentions(children);
      return parsed.length === 1 && typeof parsed[0] === 'string' ? (
        parsed[0]
      ) : (
        <>{parsed}</>
      );
    }
    if (Array.isArray(children)) {
      return children.map((child, i) => {
        if (typeof child === 'string') {
          const parsed = parseTextWithPathMentions(child);
          return parsed.length === 1 && typeof parsed[0] === 'string' ? (
            parsed[0]
          ) : (
            <span key={i}>{parsed}</span>
          );
        }
        return child;
      });
    }
    return children;
  };

  // Memoize components once. They will read from refs to get latest data.
  const components: Components = useMemo(() => {
    return {
      p: ({ children, node, ...props }) => (
        <p style={stylesRef.current.p ?? DEFAULT_STYLES.p} {...props}>
          {processChildren(children)}
        </p>
      ),
      h1: ({ node, ...props }) => (
        <h1 style={stylesRef.current.h1 ?? DEFAULT_STYLES.h1} {...props} />
      ),
      h2: ({ node, ...props }) => (
        <h2 style={stylesRef.current.h2 ?? DEFAULT_STYLES.h2} {...props} />
      ),
      h3: ({ node, ...props }) => (
        <h3 style={stylesRef.current.h3 ?? DEFAULT_STYLES.h3} {...props} />
      ),
      ul: ({ node, ...props }) => (
        <ul style={stylesRef.current.ul ?? DEFAULT_STYLES.ul} {...props} />
      ),
      ol: ({ node, ...props }) => (
        <ol style={stylesRef.current.ol ?? DEFAULT_STYLES.ol} {...props} />
      ),
      li: ({ children, node, ...props }) => (
        <li style={stylesRef.current.li ?? DEFAULT_STYLES.li} {...props}>
          {processChildren(children)}
        </li>
      ),
      table: ({ node, ...props }) => (
        <table
          style={stylesRef.current.table ?? DEFAULT_STYLES.table}
          {...props}
        />
      ),
      thead: ({ node, ...props }) => (
        <thead
          style={stylesRef.current.thead ?? DEFAULT_STYLES.thead}
          {...props}
        />
      ),
      tbody: ({ node, ...props }) => <tbody {...props} />,
      tr: ({ node, ...props }) => (
        <tr style={stylesRef.current.tr ?? DEFAULT_STYLES.tr} {...props} />
      ),
      th: ({ node, ...props }) => (
        <th style={stylesRef.current.th ?? DEFAULT_STYLES.th} {...props} />
      ),
      td: ({ node, ...props }) => (
        <td style={stylesRef.current.td ?? DEFAULT_STYLES.td} {...props} />
      ),

      a: ({ href, children, node, ...props }) => {
        const text =
          typeof children === 'string'
            ? children
            : Array.isArray(children) &&
                children.length === 1 &&
                typeof children[0] === 'string'
              ? children[0]
              : null;

        const isCitation = !!text && /^\d+$/.test(text.trim());

        const handleMouseEnter = (e: React.MouseEvent) => {
          if (href && handlersRef.current.onLinkEnter)
            handlersRef.current.onLinkEnter(href, e);
        };
        const handleMouseLeave = (e: React.MouseEvent) => {
          if (handlersRef.current.onLinkLeave)
            handlersRef.current.onLinkLeave(e);
        };
        const handleMouseMove = (e: React.MouseEvent) => {
          if (href && handlersRef.current.onLinkMove)
            handlersRef.current.onLinkMove(href, e);
        };

        if (isCitation) {
          return (
            <a
              href={href}
              target='_blank'
              rel='noopener noreferrer'
              className='citation-reference'
              onMouseEnter={handleMouseEnter}
              onMouseLeave={handleMouseLeave}
              onMouseMove={handleMouseMove}
              {...props}
            >
              {text}
            </a>
          );
        }

        return (
          <a
            href={href}
            target='_blank'
            rel='noopener noreferrer'
            style={stylesRef.current.link ?? DEFAULT_STYLES.link}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
            onMouseMove={handleMouseMove}
            {...props}
          >
            {children}
          </a>
        );
      },
    };
  }, []);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      components={components}
    >
      {content}
    </ReactMarkdown>
  );
}

export default memo(MarkdownRenderer);
