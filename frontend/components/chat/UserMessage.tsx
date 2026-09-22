import { Copy, Check } from 'lucide-react';
import { CSSProperties, useState } from 'react';
import { Dots } from '@/components/loading';
import MarkdownRenderer from './MarkdownRenderer';

// Types
interface Message {
  content: string;
  timestamp?: Date;
}

export interface UserMessageProps {
  message: Message;
  showAvatar?: boolean;
  showBorder?: boolean;
  isTyping?: boolean;
}

export default function UserMessage({
  message,
  showAvatar = true,
  showBorder = true,
  isTyping = false,
}: UserMessageProps) {
  const [isHovered, setIsHovered] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(message.content);
      } else if (typeof document !== 'undefined') {
        const textarea = document.createElement('textarea');
        textarea.value = message.content;
        textarea.style.position = 'fixed';
        textarea.style.left = '-9999px';
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const styles: { [key: string]: CSSProperties } = {
    container: {
      display: 'flex',
      alignItems: 'flex-start',
      gap: '0px',
      width: '100%',
      flexDirection: 'row-reverse',
    },
    messageWrapper: {
      position: 'relative',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'flex-end',
      width: '100%',
      maxWidth: '100%',
    },
    bubble: {
      width: '100%',
      padding: '8px 12px',
      borderRadius: '8px',
      boxShadow: 'none',
      position: 'relative',
      background: 'var(--po-hover)',
      color: 'var(--po-text)',
      border: '1px solid color-mix(in srgb, var(--po-divider) 62%, transparent)',
      cursor: 'default',
    },
    content: {
      fontSize: '14px',
      whiteSpace: 'normal',
      lineHeight: '1.5',
      margin: 0,
      textAlign: 'left',
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
    table: {
      borderCollapse: 'collapse',
      width: '100%',
      margin: '12px 0',
      fontSize: '12px',
      border: '1px solid var(--po-border)',
      backgroundColor: 'var(--po-panel-raised)',
      borderRadius: '6px',
      overflow: 'hidden',
    },
    thead: { backgroundColor: 'var(--po-border)' },
    th: {
      padding: '10px 12px',
      textAlign: 'left',
      borderBottom: '2px solid var(--po-border-strong)',
      borderRight: '1px solid var(--po-border)',
      fontWeight: 600,
      color: 'var(--po-text)',
      backgroundColor: 'var(--po-border)',
    },
    td: {
      padding: '8px 12px',
      borderBottom: '1px solid var(--po-border)',
      borderRight: '1px solid var(--po-border)',
      color: 'var(--po-text)',
      verticalAlign: 'top',
    },
    tr: {
      borderBottom: '1px solid var(--po-border)',
    },
    metaBar: {
      position: 'absolute',
      top: '100%',
      right: 0,
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      marginTop: '4px',
      opacity: isHovered ? 0.6 : 0,
      transition: 'opacity 0.2s ease',
      justifyContent: 'flex-end',
    },
    timestamp: { fontSize: '12px', color: 'var(--po-text-subtle)' },
    copyButton: {
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      width: '20px',
      height: '20px',
      borderRadius: '4px',
      color: 'var(--po-text-subtle)',
      cursor: 'pointer',
    },
    typingDots: {
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      height: '20px',
    },
  };

  return (
    <div style={styles.container}>
      <div
        style={styles.messageWrapper}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        <div style={styles.bubble}>
          {isTyping ? (
            <div style={styles.typingDots}>
              <Dots size="sm" tone="info" ariaLabel="Typing" />
            </div>
          ) : (
            <div style={styles.content}>
              <MarkdownRenderer
                content={(message.content || '')
                  .replace(/\r\n/g, '\n')
                  .replace(/\n{3,}/g, '\n\n')}
                componentsStyle={{
                  p: { margin: 0, lineHeight: '1.5', fontSize: '14px' },
                  h1: styles.h1,
                  h2: styles.h2,
                  h3: styles.h3,
                  ul: { margin: '8px 0', paddingLeft: '20px' },
                  ol: { margin: '8px 0', paddingLeft: '20px' },
                  li: { margin: '4px 0' },
                  table: styles.table,
                  thead: styles.thead,
                  tr: styles.tr,
                  th: styles.th,
                  td: styles.td,
                }}
              />
            </div>
          )}
        </div>

        {!isTyping && (
          <div style={styles.metaBar}>
            <div style={styles.timestamp}>
              {message.timestamp
                ? message.timestamp.toLocaleTimeString('en-US', {
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : ''}
            </div>
            <div
              style={styles.copyButton}
              title={copied ? 'Copied' : 'Copy message'}
              onClick={handleCopy}
            >
              {copied ? (
                <div
                  style={{
                    width: '18px',
                    height: '18px',
                    borderRadius: '50%',
                    backgroundColor: 'var(--po-text)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Check
                    style={{ width: '12px', height: '12px', color: 'var(--po-text-inverse)' }}
                  />
                </div>
              ) : (
                <Copy style={{ width: '14px', height: '14px' }} />
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
