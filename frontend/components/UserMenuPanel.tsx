'use client';

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../app/supabase/SupabaseAuthProvider';
import {
  getGithubStatus,
  disconnectGithub,
  type GithubStatusResponse,
  getGoogleSheetsStatus,
  disconnectGoogleSheets,
  type GoogleSheetsStatusResponse,
  getGmailStatus,
  disconnectGmail,
  getGoogleCalendarStatus,
  getGoogleDriveStatus,
  getGoogleDocsStatus,
  disconnectGoogleDocs,
  openOAuthPopup,
  type SaasType,
} from '../lib/oauthApi';
import { refreshProjects } from '../lib/hooks/useData';
import { instantiateTemplate } from '../lib/templatesApi';
import { useOrganization } from '@/contexts/OrganizationContext';
import { PageLoading, SkeletonBlock } from './loading';
import { ThemeToggle } from './theme/ThemeToggle';
import { ActionButton } from './ui/ActionButton';
import { ToggleSwitch } from './ui/ToggleSwitch';
import { ModalPortal } from './ui/ModalPortal';
import { StatusIndicator } from './ui/StatusDot';
import { APP_VERSION_LABEL } from '@/lib/appVersion';
import { APP_Z_INDEX } from '@/lib/zIndex';

const SHOW_DEV_TOOLS = process.env.NEXT_PUBLIC_DEV_MODE === 'true';

// ─── Local design tokens ─────────────────────────────────────────────
//
// The original panel used a one-off palette (gradient cards, var(--po-border)
// borders, vivid var(--po-success) everywhere) that read as a different product
// from the rest of the app. We now mirror the same neutral palette
// used by the access page (`./access/lib/tokens.ts`) — same hairline
// alpha, same text greys, same card surface — so flipping into this
// modal feels like the same product surface, not a separate dialog.
const T = {
  cardBg: 'var(--po-panel)',
  cardBorder: 'var(--po-border-subtle)',
  cardBorderHover: 'var(--po-border-strong)',
  border: 'var(--po-border)',
  rowHoverBg: 'var(--po-hover)',
  text1: 'var(--po-text)',
  text2: 'var(--po-text-muted)',
  text3: 'var(--po-text-disabled)',
  success: 'var(--po-success)',
  danger: 'var(--po-danger)',
  fontSans:
    'var(--po-font-sans)',
  fontMono:
    'var(--po-font-mono)',
} as const;

interface UserMenuPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

// Platform types for integrations tab
type PlatformId = 'github' | 'google-sheets' | 'google-docs' | 'gmail' | 'google-calendar' | 'google-drive';

type PlatformStatusType = 'disconnected' | 'connected' | 'error';

type PlatformState = {
  status: PlatformStatusType;
  label: string;
  isLoading: boolean;
};

type PlatformConfig = {
  id: PlatformId;
  name: string;
  description: string;
  icon: JSX.Element;
};

const platformConfigs: PlatformConfig[] = [
  {
    id: 'github',
    name: 'GitHub',
    description: 'Issues, projects, repos',
    icon: (
      <svg width='18' height='18' viewBox='0 0 24 24' fill='currentColor'>
        <path d='M12 2C6.477 2 2 6.59 2 12.253c0 4.51 2.865 8.332 6.839 9.69.5.1.683-.223.683-.495 0-.244-.01-1.051-.015-1.905-2.782.615-3.369-1.215-3.369-1.215-.455-1.185-1.11-1.5-1.11-1.5-.908-.636.069-.623.069-.623 1.002.072 1.53 1.058 1.53 1.058.893 1.567 2.343 1.115 2.914.853.091-.663.35-1.115.636-1.372-2.221-.259-4.555-1.136-4.555-5.056 0-1.117.387-2.03 1.024-2.746-.103-.26-.444-1.303.098-2.716 0 0 .837-.272 2.744 1.048a9.205 9.205 0 0 1 2.5-.346c.848.004 1.705.118 2.505.346 1.905-1.32 2.741-1.048 2.741-1.048.544 1.413.203 2.456.1 2.716.64.716 1.023 1.629 1.023 2.746 0 3.931-2.338 4.794-4.566 5.047.36.318.68.94.68 1.896 0 1.368-.013 2.471-.013 2.809 0 .274.18.598.688.495C19.138 20.582 22 16.761 22 12.253 22 6.59 17.523 2 12 2z' />
      </svg>
    ),
  },
  {
    id: 'google-sheets',
    name: 'Google Sheets',
    description: 'Spreadsheets, worksheets',
    icon: (
      <img src="/icons/google_sheet.svg" alt="Google Sheets" width={18} height={18} style={{ display: 'block' }} />
    ),
  },
  {
    id: 'google-docs',
    name: 'Google Docs',
    description: 'Documents, notes',
    icon: (
      <img src="/icons/google_doc.svg" alt="Google Docs" width={18} height={18} style={{ display: 'block' }} />
    ),
  },
  {
    id: 'gmail',
    name: 'Gmail',
    description: 'Emails, contacts',
    icon: (
      <img src="/icons/gmail.svg" alt="Gmail" width={18} height={18} style={{ display: 'block' }} />
    ),
  },
  {
    id: 'google-calendar',
    name: 'Google Calendar',
    description: 'Events, schedules',
    icon: (
      <img src="/icons/google_calendar.svg" alt="Google Calendar" width={18} height={18} style={{ display: 'block' }} />
    ),
  },
  // Google Drive temporarily hidden - not yet implemented
  // {
  //   id: 'google-drive',
  //   name: 'Google Drive',
  //   description: 'Files, folders',
  //   icon: (
  //     <svg width='18' height='18' viewBox='0 0 24 24' fill='currentColor'>
  //       <path d='M7.71 3.5L1.15 15l2.29 4.01L10 7.5 7.71 3.5zm6.58 0l-6.58 11h6.58l6.58-11h-6.58zm2.56 11.5L22.85 15l-2.14 3.75-3.36-3.75h-.5z' />
  //     </svg>
  //   ),
  // },
];

const getDefaultPlatformStates = (): Record<PlatformId, PlatformState> =>
  platformConfigs.reduce(
    (acc, platform) => {
      acc[platform.id] = {
        status: 'disconnected',
        label: 'Not connected',
        isLoading: false,
      };
      return acc;
    },
    {} as Record<PlatformId, PlatformState>
  );

export default function UserMenuPanel({ isOpen, onClose }: UserMenuPanelProps) {
  const router = useRouter();
  const { session, signOut, isAuthReady } = useAuth();
  const { currentOrg } = useOrganization();
  const [isRendered, setIsRendered] = React.useState(false);
  const [animateIn, setAnimateIn] = React.useState(false);
  const [activeTab, setActiveTab] = React.useState<'account' | 'appearance' | 'integrations' | 'about'>(
    'account'
  );

  const accountIdentityLoading = !isAuthReady || !session;
  const email = session?.user?.email ?? '';
  const userMeta = (session?.user?.user_metadata ?? {}) as Record<
    string,
    unknown
  >;
  const userName =
    (typeof userMeta?.['name'] === 'string' && userMeta['name']) ||
    (typeof userMeta?.['full_name'] === 'string' && userMeta['full_name']) ||
    (email.includes('@') ? email.split('@')[0] : 'Account');

  // Integration states
  const [githubStatus, setGithubStatus] = useState<GithubStatusResponse>({ connected: false });
  const [googleSheetsStatus, setGoogleSheetsStatus] = useState<GoogleSheetsStatusResponse>({ connected: false });
  const [gmailStatus, setGmailStatus] = useState<{ connected: boolean; email?: string }>({ connected: false });
  const [googleCalendarStatus, setGoogleCalendarStatus] = useState<{ connected: boolean; email?: string }>({ connected: false });
  const [googleDriveStatus, setGoogleDriveStatus] = useState<{ connected: boolean; email?: string }>({ connected: false });
  const [googleDocsStatus, setGoogleDocsStatus] = useState<{ connected: boolean; email?: string }>({ connected: false });
  const [platformStates, setPlatformStates] = useState<Record<PlatformId, PlatformState>>(() => getDefaultPlatformStates());
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [starterRepoLoading, setStarterRepoLoading] = useState(false);
  const [starterRepoError, setStarterRepoError] = useState<string | null>(null);
  const starterRepoOperationKey = useRef<string | null>(null);
  const [disconnectConfirmation, setDisconnectConfirmation] = useState<{
    visible: boolean;
    platformId: PlatformId | null;
  }>({ visible: false, platformId: null });

  const getPlatformName = useCallback((platformId: PlatformId) => {
    return platformConfigs.find(platform => platform.id === platformId)?.name ?? platformId;
  }, []);

  const updatePlatformState = useCallback(
    (platformId: PlatformId, updates: Partial<PlatformState>) => {
      setPlatformStates(prev => ({
        ...prev,
        [platformId]: { ...prev[platformId], ...updates },
      }));
    },
    []
  );

  const checkGithubStatus = useCallback(async () => {
    updatePlatformState('github', { isLoading: true });
    try {
      const status = await getGithubStatus();
      setGithubStatus(status);
      updatePlatformState('github', {
        status: status.connected ? 'connected' : 'disconnected',
        label: status.connected ? (status.username ? `Connected to ${status.username}` : 'Connected') : 'Not connected',
        isLoading: false,
      });
    } catch {
      updatePlatformState('github', { status: 'error', label: 'Authorization error', isLoading: false });
    }
  }, [updatePlatformState]);

  const checkGoogleSheetsStatus = useCallback(async () => {
    updatePlatformState('google-sheets', { isLoading: true });
    try {
      const status = await getGoogleSheetsStatus();
      setGoogleSheetsStatus(status);
      updatePlatformState('google-sheets', {
        status: status.connected ? 'connected' : 'disconnected',
        label: status.connected ? (status.workspace_name ? `Connected to ${status.workspace_name}` : 'Connected') : 'Not connected',
        isLoading: false,
      });
    } catch {
      updatePlatformState('google-sheets', { status: 'error', label: 'Authorization error', isLoading: false });
    }
  }, [updatePlatformState]);

  const checkGmailStatus = useCallback(async () => {
    updatePlatformState('gmail', { isLoading: true });
    try {
      const status = await getGmailStatus();
      setGmailStatus(status);
      updatePlatformState('gmail', {
        status: status.connected ? 'connected' : 'disconnected',
        label: status.connected ? (status.email ? `Connected to ${status.email}` : 'Connected') : 'Not connected',
        isLoading: false,
      });
    } catch {
      updatePlatformState('gmail', { status: 'error', label: 'Authorization error', isLoading: false });
    }
  }, [updatePlatformState]);

  const checkGoogleCalendarStatus = useCallback(async () => {
    updatePlatformState('google-calendar', { isLoading: true });
    try {
      const status = await getGoogleCalendarStatus();
      setGoogleCalendarStatus(status);
      updatePlatformState('google-calendar', {
        status: status.connected ? 'connected' : 'disconnected',
        label: status.connected ? (status.email ? `Connected to ${status.email}` : 'Connected') : 'Not connected',
        isLoading: false,
      });
    } catch {
      updatePlatformState('google-calendar', { status: 'error', label: 'Authorization error', isLoading: false });
    }
  }, [updatePlatformState]);

  const checkGoogleDriveStatus = useCallback(async () => {
    updatePlatformState('google-drive', { isLoading: true });
    try {
      const status = await getGoogleDriveStatus();
      setGoogleDriveStatus(status);
      updatePlatformState('google-drive', {
        status: status.connected ? 'connected' : 'disconnected',
        label: status.connected ? (status.email ? `Connected to ${status.email}` : 'Connected') : 'Not connected',
        isLoading: false,
      });
    } catch {
      updatePlatformState('google-drive', { status: 'error', label: 'Authorization error', isLoading: false });
    }
  }, [updatePlatformState]);

  const checkGoogleDocsStatus = useCallback(async () => {
    updatePlatformState('google-docs', { isLoading: true });
    try {
      const status = await getGoogleDocsStatus();
      setGoogleDocsStatus(status);
      updatePlatformState('google-docs', {
        status: status.connected ? 'connected' : 'disconnected',
        label: status.connected ? (status.email ? `Connected to ${status.email}` : 'Connected') : 'Not connected',
        isLoading: false,
      });
    } catch {
      updatePlatformState('google-docs', { status: 'error', label: 'Authorization error', isLoading: false });
    }
  }, [updatePlatformState]);

  const platformToSaasType: Record<PlatformId, SaasType> = {
    'github': 'github',
    'google-sheets': 'google_sheets',
    'google-docs': 'google_docs',
    'gmail': 'gmail',
    'google-calendar': 'google_calendar',
    'google-drive': 'google_drive',
  };

  const platformStatusCheckers: Record<PlatformId, () => Promise<void>> = {
    'github': checkGithubStatus,
    'google-sheets': checkGoogleSheetsStatus,
    'google-docs': checkGoogleDocsStatus,
    'gmail': checkGmailStatus,
    'google-calendar': checkGoogleCalendarStatus,
    'google-drive': checkGoogleDriveStatus,
  };

  const startOAuthConnect = async (platformId: PlatformId) => {
    const saasType = platformToSaasType[platformId];
    const platformName = getPlatformName(platformId);

    updatePlatformState(platformId, { isLoading: true, label: `Connecting to ${platformName}…` });

    try {
      const completed = await openOAuthPopup(saasType);

      if (completed) {
        await platformStatusCheckers[platformId]();
      } else {
        updatePlatformState(platformId, { isLoading: false, label: 'Authorization cancelled' });
        setTimeout(() => { platformStatusCheckers[platformId](); }, 2000);
      }
    } catch {
      updatePlatformState(platformId, { status: 'error', label: 'Authorization error', isLoading: false });
    }
  };

  const handleDisconnectConfirm = async () => {
    const platformId = disconnectConfirmation.platformId;
    if (!platformId) {
      closeDisconnectModal();
      return;
    }

    updatePlatformState(platformId, { isLoading: true, label: 'Disconnecting…' });
    try {
      if (platformId === 'github') {
        await disconnectGithub();
        setGithubStatus({ connected: false });
      } else if (platformId === 'google-sheets') {
        await disconnectGoogleSheets();
        setGoogleSheetsStatus({ connected: false });
      } else if (platformId === 'google-docs') {
        await disconnectGoogleDocs();
        setGoogleDocsStatus({ connected: false });
      } else if (platformId === 'gmail') {
        await disconnectGmail();
        setGmailStatus({ connected: false });
      } else if (platformId === 'google-calendar') {
        setGoogleCalendarStatus({ connected: false });
      } else if (platformId === 'google-drive') {
        setGoogleDriveStatus({ connected: false });
      }

      updatePlatformState(platformId, { status: 'disconnected', label: 'Not connected', isLoading: false });
    } catch {
      updatePlatformState(platformId, { status: 'error', label: 'Authorization error', isLoading: false });
    } finally {
      closeDisconnectModal();
    }
  };

  const handlePlatformToggle = (platformId: PlatformId, nextChecked: boolean) => {
    const state = platformStates[platformId];
    if (!state || state.isLoading) return;

    if (nextChecked) {
      if (state.status === 'connected') return;
      void startOAuthConnect(platformId);
    } else {
      setDisconnectConfirmation({ visible: true, platformId });
    }
  };

  const handleCreateStarterRepo = useCallback(async () => {
    if (starterRepoLoading) return;

    setStarterRepoLoading(true);
    setStarterRepoError(null);
    try {
      if (!currentOrg?.id) {
        throw new Error('Select an organization before creating a project.');
      }
      starterRepoOperationKey.current ??= crypto.randomUUID();
      const result = await instantiateTemplate('get-started', {
        org_id: currentOrg.id,
        name: 'Get Started',
        description:
          'A guided starter repo seeded from the new-user onboarding template.',
        idempotencyKey: starterRepoOperationKey.current,
      });
      starterRepoOperationKey.current = null;
      const created = result.project;
      await refreshProjects(currentOrg.id);
      onClose();
      router.push(`/projects/${created.id}/data`);
    } catch (error) {
      setStarterRepoError(
        error instanceof Error
          ? error.message
          : 'Failed to create Get Started repo',
      );
    } finally {
      setStarterRepoLoading(false);
    }
  }, [currentOrg?.id, onClose, router, starterRepoLoading]);

  const closeDisconnectModal = () => {
    setDisconnectConfirmation({ visible: false, platformId: null });
  };

  // Load integration status when tab becomes active
  useEffect(() => {
    if (activeTab === 'integrations' && isOpen) {
      const checkAllPlatformStatus = async () => {
        setIsInitialLoading(true);
        await Promise.allSettled([
          checkGithubStatus(),
          checkGoogleSheetsStatus(),
          checkGoogleDocsStatus(),
          checkGmailStatus(),
          checkGoogleCalendarStatus(),
          checkGoogleDriveStatus(),
        ]);
        setIsInitialLoading(false);
      };
      void checkAllPlatformStatus();
    }
  }, [activeTab, isOpen, checkGithubStatus, checkGoogleSheetsStatus, checkGoogleDocsStatus, checkGmailStatus, checkGoogleCalendarStatus, checkGoogleDriveStatus]);

  // ESC to close
  React.useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  // Animation handling
  React.useEffect(() => {
    if (isOpen) {
      setIsRendered(true);
      setAnimateIn(false);
      let raf1 = 0;
      let raf2 = 0;
      raf1 = requestAnimationFrame(() => {
        raf2 = requestAnimationFrame(() => setAnimateIn(true));
      });
      return () => {
        cancelAnimationFrame(raf1);
        cancelAnimationFrame(raf2);
      };
    } else {
      setAnimateIn(false);
      const t = setTimeout(() => setIsRendered(false), 450);
      return () => clearTimeout(t);
    }
  }, [isOpen]);

  if (!isRendered) return null;

  // Left-rail nav row. Pulls its row spec from the AppSidebar so the
  // two rails look like the same widget when stacked. 32px tall,
  // 13px / 500 active label, translucent fills (white/[0.06] active,
  // white/[0.03] hover) — replaces the earlier hardcoded `#1F1F1F`
  // active block which read as a different visual language than the
  // rest of the app.
  const NavBtn = ({
    id,
    label,
    icon,
  }: {
    id: 'account' | 'appearance' | 'integrations' | 'about';
    label: string;
    icon?: React.ReactNode;
  }) => {
    const isActive = activeTab === id;
    return (
      <button
        onClick={() => setActiveTab(id)}
        style={{
          width: '100%',
          height: 32,
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '0 10px',
          borderRadius: 6,
          border: 'none',
          background: isActive ? 'var(--po-border-subtle)' : 'transparent',
          color: isActive ? T.text1 : T.text2,
          cursor: 'pointer',
          transition: 'background 150ms ease, color 150ms ease',
          fontSize: 13,
          fontWeight: isActive ? 500 : 400,
          fontFamily: T.fontSans,
          textAlign: 'left',
        }}
        onMouseEnter={e => {
          if (isActive) return;
          e.currentTarget.style.background = 'var(--po-hover)';
          e.currentTarget.style.color = T.text1;
        }}
        onMouseLeave={e => {
          if (isActive) return;
          e.currentTarget.style.background = 'transparent';
          e.currentTarget.style.color = T.text2;
        }}
      >
        <span
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 16,
            height: 16,
            flexShrink: 0,
            color: isActive ? T.text1 : T.text2,
          }}
        >
          {icon}
        </span>
        <span style={{ flex: 1, minWidth: 0, color: 'inherit' }}>{label}</span>
      </button>
    );
  };

  // Social links
  const socialLinks = [
    {
      name: 'GitHub',
      url: 'https://github.com/puppyone-ai/puppyone-cloud',
      icon: (
        <svg width='16' height='16' viewBox='0 0 24 24' fill='currentColor'>
          <path d='M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z' />
        </svg>
      ),
    },
    {
      name: 'X (Twitter)',
      url: 'https://x.com/puppyone_ai',
      icon: (
        <svg width='16' height='16' viewBox='0 0 24 24' fill='currentColor'>
          <path d='M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z' />
        </svg>
      ),
    },
    {
      name: 'YouTube',
      url: 'https://www.youtube.com/@PuppyAgent',
      icon: (
        <svg width='16' height='16' viewBox='0 0 24 24' fill='currentColor'>
          <path d='M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z' />
        </svg>
      ),
    },
    {
      name: 'LinkedIn',
      url: 'https://www.linkedin.com/company/puppyone',
      icon: (
        <svg width='16' height='16' viewBox='0 0 24 24' fill='currentColor'>
          <path d='M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z' />
        </svg>
      ),
    },
    {
      name: 'Discord',
      url: 'https://discord.gg/zwJ9Y3Uvpd',
      icon: (
        <svg width='16' height='16' viewBox='0 0 24 24' fill='currentColor'>
          <path d='M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z' />
        </svg>
      ),
    },
  ];

  return (
    <ModalPortal>
    <div
      role='dialog'
      aria-modal='true'
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: APP_Z_INDEX.modal,
        opacity: animateIn ? 1 : 0,
        transition: 'opacity 450ms cubic-bezier(0.22, 1, 0.36, 1)',
      }}
    >
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'var(--po-backdrop)',
          backdropFilter: 'blur(2px)',
          opacity: animateIn ? 1 : 0,
          transition: 'opacity 400ms ease',
        }}
      />

      {/* Panel — flat surface with a translucent hairline. The
          earlier copy used a dark-only gradient plus a hardcoded border,
          which is a treatment nothing else in the product uses; it
          made the modal feel like a different surface from
          everything around it. The flat `var(--po-panel)` over `T.border`
          + a soft drop-shadow now matches the rest of the chrome
          (page cards, dropdown menus, dialogs). */}
      <div
        onClick={e => e.stopPropagation()}
        style={{
          position: 'fixed',
          top: '50%',
          left: '50%',
          transform: `translate(-50%, -50%) scale(${animateIn ? 1 : 0.97})`,
          opacity: animateIn ? 1 : 0,
          width: 'min(720px, 96vw)',
          height: '480px',
          overflow: 'hidden',
          background: 'var(--po-panel)',
          border: `1px solid ${T.border}`,
          borderRadius: 14,
          boxShadow:
            '0 20px 48px var(--po-shadow), 0 0 0 1px var(--po-hover)',
          transition: 'all 400ms cubic-bezier(0.22, 1, 0.36, 1)',
        }}
      >
        <div
          style={{
            display: 'flex',
            height: '100%',
            fontSize: 13,
            color: T.text1,
            fontFamily: T.fontSans,
          }}
        >
          {/* Left Navigation — uses the same border-alpha as every
              other rail divider in the product. */}
          <div
            style={{
              width: 176,
              height: '100%',
              borderRight: `1px solid ${T.border}`,
              background: 'transparent',
              padding: '12px 0',
            }}
          >
            <nav
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: 2,
                padding: '0 8px',
              }}
            >
              <NavBtn
                id='account'
                label='Account'
                icon={
                  <svg
                    width='16'
                    height='16'
                    viewBox='0 0 24 24'
                    fill='none'
                    stroke='currentColor'
                    strokeWidth='2'
                    strokeLinecap='round'
                    strokeLinejoin='round'
                  >
                    <circle cx='12' cy='8' r='4' />
                    <path d='M4 22c0-4 4-7 8-7s8 3 8 7' />
                  </svg>
                }
              />
              <NavBtn
                id='appearance'
                label='Appearance'
                icon={
                  <svg
                    width='16'
                    height='16'
                    viewBox='0 0 24 24'
                    fill='none'
                    stroke='currentColor'
                    strokeWidth='2'
                    strokeLinecap='round'
                    strokeLinejoin='round'
                  >
                    <circle cx='12' cy='12' r='4' />
                    <path d='M12 2v2' />
                    <path d='M12 20v2' />
                    <path d='m4.93 4.93 1.41 1.41' />
                    <path d='m17.66 17.66 1.41 1.41' />
                    <path d='M2 12h2' />
                    <path d='M20 12h2' />
                    <path d='m6.34 17.66-1.41 1.41' />
                    <path d='m19.07 4.93-1.41 1.41' />
                  </svg>
                }
              />
              <NavBtn
                id='integrations'
                label='Integrations'
                icon={
                  <svg
                    width='16'
                    height='16'
                    viewBox='0 0 24 24'
                    fill='none'
                    stroke='currentColor'
                    strokeWidth='2'
                    strokeLinecap='round'
                    strokeLinejoin='round'
                  >
                    <path d='M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83' />
                  </svg>
                }
              />
              <NavBtn
                id='about'
                label='About'
                icon={
                  <svg
                    width='16'
                    height='16'
                    viewBox='0 0 24 24'
                    fill='none'
                    stroke='currentColor'
                    strokeWidth='2'
                    strokeLinecap='round'
                    strokeLinejoin='round'
                  >
                    <circle cx='12' cy='12' r='10' />
                    <line x1='12' y1='16' x2='12' y2='12' />
                    <line x1='12' y1='8' x2='12.01' y2='8' />
                  </svg>
                }
              />
            </nav>
          </div>

          {/* Right Content */}
          <div
            style={{
              flex: 1,
              display: 'flex',
              flexDirection: 'column',
              padding: '20px 20px 24px',
              overflowY: 'auto',
              minHeight: 0,
            }}
          >
            {/* Tab heading — 14px / 600 / `T.text1`. A notch smaller
                than the previous 16px so the heading doesn't shout
                over the 15px card titles directly below it
                (workspace name / billing handoff), and the modal feels
                like the same scale as the page-level Settings header
                (13px / 500 across /(main)). */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'flex-start',
                marginBottom: 14,
              }}
            >
              <div
                style={{
                  fontSize: 14,
                  fontWeight: 600,
                  color: T.text1,
                  letterSpacing: '-0.005em',
                }}
              >
                {activeTab === 'account'
                  ? 'Account'
                  : activeTab === 'appearance'
                  ? 'Appearance'
                  : activeTab === 'integrations'
                  ? 'Integrations'
                  : 'About'}
              </div>
            </div>

            {/* Account Tab */}
            {activeTab === 'account' && (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 12,
                }}
              >
                {/* Account info card — neutral surface, no gradient.
                    Uses the same `cardBg / cardBorder` pair as the
                    access-page connector cards so the panel reads as
                    one consistent product chrome. */}
                <div
                  style={{
                    border: `1px solid ${T.cardBorder}`,
                    borderRadius: 10,
                    background: T.cardBg,
                    padding: 20,
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 12,
                      flexWrap: 'wrap',
                    }}
                  >
                    <div
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 4,
                        minWidth: 0,
                      }}
                    >
                      <div
                        style={{
                          color: T.text1,
                          fontSize: 15,
                          fontWeight: 600,
                          lineHeight: 1.3,
                        }}
                      >
                        {accountIdentityLoading ? (
                          <SkeletonBlock width={140} height={12} radius={3} />
                        ) : (
                          userName
                        )}
                      </div>
                      <div
                        style={{
                          color: T.text2,
                          fontSize: 12,
                          fontFamily: T.fontMono,
                        }}
                      >
                        {accountIdentityLoading ? (
                          <SkeletonBlock width={180} height={10} radius={3} />
                        ) : (
                          email
                        )}
                      </div>
                    </div>
                    <button
                      type='button'
                      onClick={signOut}
                      disabled={accountIdentityLoading}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        height: 30,
                        padding: '0 10px',
                        borderRadius: 6,
                        border: `1px solid ${T.border}`,
                        background: 'transparent',
                        color: T.text2,
                        fontSize: 12,
                        fontWeight: 500,
                        fontFamily: T.fontSans,
                        cursor: accountIdentityLoading ? 'not-allowed' : 'pointer',
                        opacity: accountIdentityLoading ? 0.5 : 1,
                        whiteSpace: 'nowrap',
                        transition:
                          'background 150ms ease, border-color 150ms ease, color 150ms ease',
                      }}
                      onMouseEnter={e => {
                        e.currentTarget.style.background = 'var(--po-hover)';
                        e.currentTarget.style.borderColor = 'var(--po-border-strong)';
                        e.currentTarget.style.color = T.text1;
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.background = 'transparent';
                        e.currentTarget.style.borderColor = T.border;
                        e.currentTarget.style.color = T.text2;
                      }}
                    >
                      Sign out
                    </button>
                  </div>
                </div>

                {/* Subscription card — same surface treatment.
                    Plan label uses the same overline/value pattern as
                    the project dashboard cards (small caps-ish label,
                    larger value). */}
                <div
                  style={{
                    border: `1px solid ${T.cardBorder}`,
                    borderRadius: 10,
                    background: T.cardBg,
                    padding: 20,
                  }}
                >
                  <div style={{ display: 'flex', flexDirection: 'column' }}>
                    <div
                      style={{
                        fontSize: 11,
                        fontWeight: 500,
                        color: T.text3,
                        marginBottom: 6,
                        letterSpacing: '0.04em',
                        textTransform: 'uppercase',
                      }}
                    >
                      Billing
                    </div>
                    <div
                      style={{
                        fontSize: 15,
                        fontWeight: 600,
                        color: T.text1,
                      }}
                    >
                      PuppyOne Desktop
                    </div>
                    <div
                      style={{
                        height: 1,
                        background: T.cardBorder,
                        margin: '12px 0',
                      }}
                    />
                    <div
                      style={{
                        fontSize: 12,
                        color: T.text2,
                        lineHeight: 1.6,
                      }}
                    >
                      View the authoritative plan, usage, and billing settings in the Desktop app.
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Appearance Tab */}
            {activeTab === 'appearance' && (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 12,
                }}
              >
                <div
                  style={{
                    border: `1px solid ${T.cardBorder}`,
                    borderRadius: 10,
                    background: T.cardBg,
                    padding: 20,
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 6,
                      marginBottom: 16,
                    }}
                  >
                    <div
                      style={{
                        fontSize: 15,
                        fontWeight: 600,
                        color: T.text1,
                        lineHeight: 1.3,
                      }}
                    >
                      Color mode
                    </div>
                    <div
                      style={{
                        maxWidth: 460,
                        fontSize: 12,
                        color: T.text2,
                        lineHeight: 1.55,
                      }}
                    >
                      Choose how Puppyone appears on this device.
                    </div>
                  </div>
                  <ThemeToggle />
                </div>
              </div>
            )}

            {/* Integrations Tab */}
            {activeTab === 'integrations' && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div
                  style={{
                    fontSize: 12,
                    color: T.text2,
                    marginBottom: 4,
                    lineHeight: 1.5,
                  }}
                >
                  Connect your accounts to import data from external services.
                </div>

                {isInitialLoading ? (
                  <div
                    style={{
                      height: 96,
                      display: 'flex',
                    }}
                  >
                    <PageLoading variant="fill" />
                  </div>
                ) : (
                  <div
                    style={{ display: 'flex', flexDirection: 'column', gap: 6 }}
                  >
                    {platformConfigs.map(platform => {
                      const state = platformStates[platform.id];
                      const isConnected = state.status === 'connected';
                      const isError = state.status === 'error';

                      return (
                        <div
                          key={platform.id}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 12,
                            padding: '10px 14px',
                            borderRadius: 8,
                            border: `1px solid ${T.cardBorder}`,
                            background: T.cardBg,
                            transition: 'border-color 150ms ease',
                          }}
                          onMouseEnter={e => {
                            e.currentTarget.style.borderColor =
                              T.cardBorderHover;
                          }}
                          onMouseLeave={e => {
                            e.currentTarget.style.borderColor = T.cardBorder;
                          }}
                        >
                          {/* Provider icon — kept neutral regardless
                              of connection status so the row's identity
                              (Gmail / GitHub / …) reads first; status
                              is communicated by the dot+text pair on
                              the right of the name. */}
                          <div
                            style={{
                              color: T.text2,
                              flexShrink: 0,
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              width: 20,
                              height: 20,
                            }}
                          >
                            {platform.icon}
                          </div>

                          {/* Name + status — status stays compact and
                              neutral so the modal feels native to the
                              rest of the chrome instead of glowing green. */}
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div
                              style={{
                                fontSize: 13,
                                fontWeight: 500,
                                color: T.text1,
                                lineHeight: 1.3,
                              }}
                            >
                              {platform.name}
                            </div>
                            <div
                              style={{
                                display: 'flex',
                                alignItems: 'center',
                                gap: 6,
                                marginTop: 3,
                                fontSize: 11,
                                color: T.text2,
                                whiteSpace: 'nowrap',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                              }}
                            >
                              <StatusIndicator
                                status={isError ? 'error' : state.status}
                                label={state.label}
                                style={{ opacity: state.isLoading ? 0.5 : 1 }}
                                textStyle={{ overflow: 'hidden', textOverflow: 'ellipsis' }}
                              />
                            </div>
                          </div>

                          <ToggleSwitch
                            checked={isConnected}
                            ariaLabel={`Toggle ${platform.name}`}
                            onCheckedChange={checked =>
                              handlePlatformToggle(platform.id, checked)
                            }
                            disabled={state.isLoading}
                          />
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {/* About Tab */}
            {activeTab === 'about' && (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 12,
                }}
              >
                <div
                  style={{
                    border: `1px solid ${T.cardBorder}`,
                    borderRadius: 10,
                    background: T.cardBg,
                    padding: 20,
                  }}
                >
                  {/* Identity row — real `/puppyone-logo.svg` (the
                      same asset rendered in the collapsed sidebar
                      chip) instead of the placeholder gradient block
                      with a generic "stack" glyph. Version comes from
                      the shared appVersion helper so chrome and About
                      never drift. */}
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 14,
                      marginBottom: 20,
                    }}
                  >
                    <img
                      src='/puppyone-logo.svg'
                      alt='Puppyone'
                      width={44}
                      height={44}
                      style={{ display: 'block', flexShrink: 0 }}
                    />
                    <div
                      style={{
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 2,
                        minWidth: 0,
                      }}
                    >
                      <div
                        style={{
                          fontSize: 15,
                          fontWeight: 600,
                          color: T.text1,
                          letterSpacing: '-0.01em',
                        }}
                      >
                        Puppyone
                      </div>
                      <div
                        style={{
                          fontSize: 11,
                          color: T.text3,
                          fontFamily: T.fontMono,
                          letterSpacing: '0.02em',
                        }}
                      >
                        Version {APP_VERSION_LABEL}
                      </div>
                    </div>
                  </div>

                  <div
                    style={{
                      height: 1,
                      background: T.cardBorder,
                      margin: '0 0 16px 0',
                    }}
                  />

                  {/* Social links — every button is a strict 32px tall
                      ghost row to match the rest of the product's
                      button rhythm (Sign-out button above, Pause /
                      Resume in the access detail pane, GhostButton
                      across the page-shell). Width is content-fit; the
                      row wraps onto a second line at narrow widths. */}
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 500,
                      color: T.text3,
                      marginBottom: 10,
                      letterSpacing: '0.04em',
                      textTransform: 'uppercase',
                    }}
                  >
                    Connect with us
                  </div>
                  <div
                    style={{
                      display: 'flex',
                      flexWrap: 'wrap',
                      gap: 8,
                    }}
                  >
                    {socialLinks.map(link => (
                      <a
                        key={link.name}
                        href={link.url}
                        target='_blank'
                        rel='noopener noreferrer'
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 6,
                          height: 30,
                          padding: '0 10px',
                          borderRadius: 6,
                          border: `1px solid ${T.border}`,
                          background: 'transparent',
                          color: T.text2,
                          fontSize: 12,
                          fontWeight: 500,
                          fontFamily: T.fontSans,
                          textDecoration: 'none',
                          transition:
                            'background 150ms ease, border-color 150ms ease, color 150ms ease',
                          boxSizing: 'border-box',
                        }}
                        onMouseEnter={e => {
                          e.currentTarget.style.background = 'var(--po-hover)';
                          e.currentTarget.style.borderColor = 'var(--po-border-strong)';
                          e.currentTarget.style.color = T.text1;
                        }}
                        onMouseLeave={e => {
                          e.currentTarget.style.background = 'transparent';
                          e.currentTarget.style.borderColor = T.border;
                          e.currentTarget.style.color = T.text2;
                        }}
                      >
                        {/* Wrapper sized to the SVGs in `socialLinks`
                            (16×16). Doesn't clip; just provides a
                            stable inline-flex slot for the glyph. */}
                        <span
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            width: 14,
                            height: 14,
                            flexShrink: 0,
                          }}
                        >
                          {React.cloneElement(link.icon as React.ReactElement, {
                            width: 14,
                            height: 14,
                          })}
                        </span>
                        <span>{link.name}</span>
                      </a>
                    ))}
                  </div>
                </div>

                {SHOW_DEV_TOOLS && (
                  <div
                    style={{
                      border: `1px solid ${T.cardBorder}`,
                      borderRadius: 10,
                      background: T.cardBg,
                      padding: 16,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 16,
                    }}
                  >
                    <div style={{ minWidth: 0 }}>
                      <div
                        style={{
                          fontSize: 13,
                          fontWeight: 600,
                          color: T.text1,
                          marginBottom: 4,
                        }}
                      >
                        Developer tools
                      </div>
                      <div
                        style={{
                          fontSize: 12,
                          lineHeight: 1.45,
                          color: T.text2,
                        }}
                      >
                        Create a fresh Get Started repo from the first-run template.
                      </div>
                      {starterRepoError && (
                        <div
                          style={{
                            marginTop: 8,
                            fontSize: 12,
                            lineHeight: 1.45,
                            color: T.danger,
                          }}
                        >
                          {starterRepoError}
                        </div>
                      )}
                    </div>
                    <ActionButton
                      variant='primary'
                      size='md'
                      loading={starterRepoLoading}
                      onClick={handleCreateStarterRepo}
                      style={{ flexShrink: 0 }}
                    >
                      Create starter repo
                    </ActionButton>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Disconnect Confirmation Modal */}
      {disconnectConfirmation.visible && disconnectConfirmation.platformId && (
        <div
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: APP_Z_INDEX.modalNested,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <div
            onClick={closeDisconnectModal}
            style={{
              position: 'absolute',
              inset: 0,
              background: 'var(--po-backdrop)',
            }}
          />
          <div
            style={{
              position: 'relative',
              background: 'var(--po-panel)',
              border: `1px solid ${T.border}`,
              borderRadius: 12,
              padding: 20,
              width: 'min(400px, 90vw)',
              boxShadow: '0 16px 48px var(--po-shadow), 0 0 0 1px var(--po-hover)',
              fontFamily: T.fontSans,
            }}
          >
            <div style={{ fontSize: 14, fontWeight: 600, color: T.text1, marginBottom: 6 }}>
              Disconnect {getPlatformName(disconnectConfirmation.platformId)}?
            </div>
            <div style={{ fontSize: 12, color: T.text2, marginBottom: 18, lineHeight: 1.55 }}>
              You will need to re-authorize to import data from this service again.
            </div>
            <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
              <button
                type="button"
                onClick={closeDisconnectModal}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  height: 30,
                  padding: '0 10px',
                  borderRadius: 6,
                  border: `1px solid ${T.border}`,
                  background: 'transparent',
                  color: T.text2,
                  fontSize: 12,
                  fontWeight: 500,
                  fontFamily: T.fontSans,
                  cursor: 'pointer',
                  transition: 'background 150ms ease, color 150ms ease, border-color 150ms ease',
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.background = 'var(--po-hover)';
                  e.currentTarget.style.borderColor = 'var(--po-border-strong)';
                  e.currentTarget.style.color = T.text1;
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = 'transparent';
                  e.currentTarget.style.borderColor = T.border;
                  e.currentTarget.style.color = T.text2;
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDisconnectConfirm}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  height: 30,
                  padding: '0 12px',
                  borderRadius: 6,
                  border: '1px solid color-mix(in srgb, var(--po-danger) 32%, transparent)',
                  background: 'color-mix(in srgb, var(--po-danger) 12%, transparent)',
                  color: 'var(--po-danger)',
                  fontSize: 12,
                  fontWeight: 500,
                  fontFamily: T.fontSans,
                  cursor: 'pointer',
                  transition: 'background 150ms ease, color 150ms ease, border-color 150ms ease',
                }}
                onMouseEnter={e => {
                  e.currentTarget.style.background = 'color-mix(in srgb, var(--po-danger) 20%, transparent)';
                  e.currentTarget.style.borderColor = 'color-mix(in srgb, var(--po-danger) 45%, transparent)';
                  e.currentTarget.style.color = 'var(--po-danger)';
                }}
                onMouseLeave={e => {
                  e.currentTarget.style.background = 'color-mix(in srgb, var(--po-danger) 12%, transparent)';
                  e.currentTarget.style.borderColor = 'color-mix(in srgb, var(--po-danger) 32%, transparent)';
                  e.currentTarget.style.color = 'var(--po-danger)';
                }}
              >
                Disconnect
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
    </ModalPortal>
  );
}
