/**
 * Application shell: fixed navigation rail, content header, content well.
 *
 * The rail carries a live system strip (API reachability, trial-balance state,
 * entry count) because an operator's first question about a ledger is always
 * "is it consistent right now?" - and the answer is real, not decorative.
 */

import {
  ArrowLeftRight,
  BookOpen,
  LayoutGrid,
  Moon,
  Plus,
  RefreshCw,
  ScrollText,
  Sun,
  Wallet,
} from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type ReactNode } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';

import { cn } from '../../lib/cn';
import { formatInteger } from '../../lib/format';
import { useHealth, useStats } from '../../lib/queries';
import { Button, IconButton, StatusDot } from '../ui/primitives';

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutGrid, end: true },
  { to: '/accounts', label: 'Accounts', icon: Wallet, end: false },
  { to: '/transactions', label: 'Transactions', icon: ArrowLeftRight, end: false },
  { to: '/ledger', label: 'Ledger explorer', icon: BookOpen, end: false },
  { to: '/audit', label: 'Audit log', icon: ScrollText, end: false },
];

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'));

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    try {
      localStorage.setItem('ledgr.theme', dark ? 'dark' : 'light');
    } catch {
      /* storage may be unavailable; the theme still applies for this session */
    }
  }, [dark]);

  return { dark, toggle: () => setDark((value) => !value) };
}

function Wordmark() {
  return (
    <div className="flex items-center gap-2.5 px-3 py-3">
      <svg viewBox="0 0 32 32" className="h-7 w-7 shrink-0" aria-hidden>
        <rect width="32" height="32" rx="6" className="fill-ink" />
        <rect x="7" y="9" width="18" height="2.4" rx="1.2" className="fill-debit" />
        <rect x="7" y="15" width="18" height="2.4" rx="1.2" className="fill-credit" />
        <rect x="7" y="21" width="11" height="2.4" rx="1.2" className="fill-ink-3" />
      </svg>
      <div className="leading-tight">
        <p className="text-[0.9375rem] font-bold tracking-[0.14em] text-ink">LEDGR</p>
        <p className="text-2xs tracking-wide text-ink-3">Ledger operations</p>
      </div>
    </div>
  );
}

function SystemStrip() {
  const health = useHealth();
  const stats = useStats();

  const apiUp = health.data?.database === 'up';
  const balanced = stats.data?.ledger_balanced ?? null;

  return (
    <div className="border-t border-border px-3 py-3">
      <p className="label mb-2">System</p>
      <dl className="space-y-1.5 text-xs">
        <div className="flex items-center justify-between gap-2">
          <dt className="text-ink-3">API</dt>
          <dd className="flex items-center gap-1.5">
            <StatusDot tone={apiUp ? 'positive' : 'negative'} pulse={apiUp} />
            <span className={apiUp ? 'text-ink-2' : 'text-negative'}>
              {health.isLoading ? 'checking' : apiUp ? 'connected' : 'unreachable'}
            </span>
          </dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-ink-3">Trial balance</dt>
          <dd className="flex items-center gap-1.5">
            <StatusDot tone={balanced === null ? 'neutral' : balanced ? 'positive' : 'negative'} />
            <span className={cn(balanced === false ? 'text-negative' : 'text-ink-2')}>
              {balanced === null ? '—' : balanced ? 'balanced' : 'unbalanced'}
            </span>
          </dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-ink-3">Ledger entries</dt>
          <dd className="num text-ink-2">
            {stats.data ? formatInteger(stats.data.entry_count) : '—'}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-ink-3">Environment</dt>
          <dd className="num text-ink-2">{health.data?.environment ?? '—'}</dd>
        </div>
      </dl>
    </div>
  );
}

function Sidebar({
  dark,
  onToggleTheme,
  onRefresh,
}: {
  dark: boolean;
  onToggleTheme: () => void;
  onRefresh: () => void;
}) {
  const navigate = useNavigate();

  return (
    <aside className="fixed inset-y-0 left-0 z-20 hidden w-[228px] flex-col border-r border-border bg-surface lg:flex">
      <Wordmark />

      <div className="px-3 pb-3">
        <Button
          variant="primary"
          className="w-full justify-start"
          icon={<Plus className="h-3.5 w-3.5" strokeWidth={2.4} />}
          onClick={() => navigate('/transactions/new')}
        >
          New transaction
        </Button>
      </div>

      <nav className="flex-1 space-y-px overflow-y-auto px-2">
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-sm px-2.5 py-1.5 text-[0.8125rem] transition-colors',
                isActive
                  ? 'bg-accent-soft font-medium text-accent-ink'
                  : 'text-ink-2 hover:bg-surface-3 hover:text-ink',
              )
            }
          >
            <Icon className="h-4 w-4 shrink-0" strokeWidth={1.7} />
            {label}
          </NavLink>
        ))}
      </nav>

      <SystemStrip />

      <div className="flex items-center justify-between border-t border-border px-3 py-2">
        <span className="num text-2xs text-ink-3">v1.0.0</span>
        <div className="flex items-center gap-0.5">
          <IconButton title="Refresh all data" onClick={onRefresh}>
            <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.8} />
          </IconButton>
          <IconButton
            title={dark ? 'Switch to light theme' : 'Switch to dark theme'}
            onClick={onToggleTheme}
          >
            {dark ? (
              <Sun className="h-3.5 w-3.5" strokeWidth={1.8} />
            ) : (
              <Moon className="h-3.5 w-3.5" strokeWidth={1.8} />
            )}
          </IconButton>
        </div>
      </div>
    </aside>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  breadcrumb,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  breadcrumb?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-10 border-b border-border bg-canvas/85 px-6 py-3 backdrop-blur-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {breadcrumb && <div className="mb-1 flex items-center gap-1.5 text-xs text-ink-3">{breadcrumb}</div>}
          <h1 className="truncate text-lg font-semibold tracking-tight text-ink">{title}</h1>
          {description && <p className="mt-0.5 text-xs text-ink-3">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { dark, toggle } = useTheme();
  const queryClient = useQueryClient();

  return (
    <div className="min-h-screen bg-canvas">
      <Sidebar
        dark={dark}
        onToggleTheme={toggle}
        onRefresh={() => queryClient.invalidateQueries()}
      />

      {/* Compact rail for narrow viewports: the console stays usable on a laptop
          split-screen without collapsing the data tables. */}
      <div className="fixed inset-x-0 top-0 z-20 flex items-center justify-between border-b border-border bg-surface px-3 py-2 lg:hidden">
        <div className="flex items-center gap-2">
          <svg viewBox="0 0 32 32" className="h-6 w-6" aria-hidden>
            <rect width="32" height="32" rx="6" className="fill-ink" />
            <rect x="7" y="9" width="18" height="2.4" rx="1.2" className="fill-debit" />
            <rect x="7" y="15" width="18" height="2.4" rx="1.2" className="fill-credit" />
          </svg>
          <span className="text-sm font-bold tracking-[0.14em]">LEDGR</span>
        </div>
        <nav className="flex items-center gap-px">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              title={label}
              className={({ isActive }) =>
                cn(
                  'flex h-7 w-7 items-center justify-center rounded-sm',
                  isActive ? 'bg-accent-soft text-accent-ink' : 'text-ink-3 hover:bg-surface-3',
                )
              }
            >
              <Icon className="h-4 w-4" strokeWidth={1.7} />
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="pt-11 lg:pl-[228px] lg:pt-0">{children}</div>
    </div>
  );
}
