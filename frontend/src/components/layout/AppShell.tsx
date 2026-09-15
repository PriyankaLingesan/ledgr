/**
 * Console application shell: fixed sidebar, content header, content well.
 *
 * The sidebar carries a live system status area (API reachability, database
 * health, trial-balance state) because an operator's first question about a
 * ledger is always "is it consistent right now?" - and every figure there is
 * read from the API, never hard-coded.
 */

import { useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeftRight,
  BookOpen,
  Info,
  LayoutGrid,
  Menu,
  Moon,
  Plus,
  RefreshCw,
  ScrollText,
  Sparkles,
  Sun,
  Wallet,
  X,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';

import { Wordmark } from '../brand/Wordmark';
import { cn } from '../../lib/cn';
import { formatInteger } from '../../lib/format';
import { useHealth, useStats } from '../../lib/queries';
import { useTheme } from '../../lib/theme';
import { Button, IconButton, StatusDot } from '../ui/primitives';

const NAV = [
  { to: '/app', label: 'Dashboard', icon: LayoutGrid, end: true },
  { to: '/app/accounts', label: 'Accounts', icon: Wallet, end: false },
  { to: '/app/transactions', label: 'Transactions', icon: ArrowLeftRight, end: false },
  { to: '/app/ledger', label: 'Ledger explorer', icon: BookOpen, end: false },
  { to: '/app/intelligence', label: 'LEDGR Intelligence', icon: Sparkles, end: false },
  { to: '/app/audit', label: 'Audit log', icon: ScrollText, end: false },
  { to: '/app/about', label: 'About', icon: Info, end: false },
];

function SystemStatus() {
  const health = useHealth();
  const stats = useStats();

  const apiUp = health.data?.status === 'ok';
  const dbUp = health.data?.database === 'up';
  const balanced = stats.data?.ledger_balanced;

  const rows: { label: string; ok: boolean | null; detail?: string }[] = [
    {
      label: 'API',
      ok: health.isLoading ? null : apiUp,
      detail: health.isLoading ? 'Checking…' : apiUp ? 'Connected' : 'Unreachable',
    },
    {
      label: 'Database',
      ok: health.isLoading ? null : dbUp,
      detail: health.isLoading ? 'Checking…' : dbUp ? 'Healthy' : 'Unreachable',
    },
    {
      label: 'Ledger',
      ok: stats.isLoading ? null : (balanced ?? null),
      detail: stats.isLoading ? 'Checking…' : balanced === undefined ? '—' : balanced ? 'Balanced' : 'Unbalanced',
    },
  ];

  return (
    <div className="border-t border-border px-4 py-3.5">
      <p className="mb-2 text-xs font-medium text-ink-3">System status</p>
      <ul className="space-y-1.5">
        {rows.map((row) => (
          <li key={row.label} className="flex items-center justify-between gap-2 text-[0.8125rem]">
            <span className="flex items-center gap-2 text-ink-2">
              <StatusDot
                tone={row.ok === null ? 'neutral' : row.ok ? 'positive' : 'negative'}
                pulse={row.ok === true}
              />
              {row.label}
            </span>
            <span className={cn(row.ok === false ? 'text-negative' : 'text-ink-3')}>{row.detail}</span>
          </li>
        ))}
      </ul>
      {stats.data && (
        <p className="mt-2.5 border-t border-border pt-2.5 text-xs text-ink-3">
          <span className="num text-ink-2">{formatInteger(stats.data.entry_count)}</span> ledger entries
        </p>
      )}
    </div>
  );
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-2">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              'flex items-center gap-2.5 rounded-sm px-2.5 py-2 text-[0.875rem] transition-colors',
              isActive
                ? 'bg-accent-soft font-medium text-accent-ink'
                : 'text-ink-2 hover:bg-surface-2 hover:text-ink',
            )
          }
        >
          <Icon className="h-4 w-4 shrink-0" strokeWidth={1.7} />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

function SidebarFooter({
  dark,
  onToggleTheme,
  onRefresh,
}: {
  dark: boolean;
  onToggleTheme: () => void;
  onRefresh: () => void;
}) {
  return (
    <div className="flex items-center justify-between border-t border-border px-4 py-2.5">
      <span className="num text-2xs text-ink-3">v1.0.0</span>
      <div className="flex items-center gap-0.5">
        <IconButton title="Refresh data" onClick={onRefresh}>
          <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.8} />
        </IconButton>
        <IconButton
          title={dark ? 'Switch to light theme' : 'Switch to dark theme'}
          aria-pressed={dark}
          onClick={onToggleTheme}
        >
          {dark ? <Sun className="h-3.5 w-3.5" strokeWidth={1.8} /> : <Moon className="h-3.5 w-3.5" strokeWidth={1.8} />}
        </IconButton>
      </div>
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
    <aside className="fixed inset-y-0 left-0 z-20 hidden w-64 flex-col border-r border-border bg-surface lg:flex">
      <div className="px-4 py-4">
        <NavLink to="/app" aria-label="LEDGR dashboard">
          <Wordmark />
        </NavLink>
      </div>

      <div className="px-3 pb-2">
        <Button
          variant="primary"
          className="w-full justify-start"
          icon={<Plus className="h-4 w-4" strokeWidth={2.2} />}
          onClick={() => navigate('/app/transactions/new')}
        >
          New transaction
        </Button>
      </div>

      <NavList />
      <SystemStatus />
      <SidebarFooter dark={dark} onToggleTheme={onToggleTheme} onRefresh={onRefresh} />
    </aside>
  );
}

function MobileTopBar({
  onOpenMenu,
  dark,
  onToggleTheme,
}: {
  onOpenMenu: () => void;
  dark: boolean;
  onToggleTheme: () => void;
}) {
  return (
    <div className="fixed inset-x-0 top-0 z-20 flex items-center justify-between border-b border-border bg-surface px-3 py-2.5 lg:hidden">
      <div className="flex items-center gap-2">
        <IconButton title="Open menu" onClick={onOpenMenu}>
          <Menu className="h-5 w-5" strokeWidth={1.8} />
        </IconButton>
        <NavLink to="/app" aria-label="LEDGR dashboard">
          <Wordmark />
        </NavLink>
      </div>
      <IconButton
        title={dark ? 'Switch to light theme' : 'Switch to dark theme'}
        aria-pressed={dark}
        onClick={onToggleTheme}
      >
        {dark ? <Sun className="h-4 w-4" strokeWidth={1.8} /> : <Moon className="h-4 w-4" strokeWidth={1.8} />}
      </IconButton>
    </div>
  );
}

function MobileDrawer({
  open,
  onClose,
  dark,
  onToggleTheme,
  onRefresh,
}: {
  open: boolean;
  onClose: () => void;
  dark: boolean;
  onToggleTheme: () => void;
  onRefresh: () => void;
}) {
  const navigate = useNavigate();

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-30 lg:hidden" role="dialog" aria-modal="true">
      <div className="fixed inset-0 bg-ink/30" onClick={onClose} aria-hidden />
      <div className="fixed inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-surface shadow-xl">
        <div className="flex items-center justify-between px-4 py-4">
          <Wordmark />
          <IconButton title="Close menu" onClick={onClose}>
            <X className="h-5 w-5" strokeWidth={1.8} />
          </IconButton>
        </div>
        <div className="px-3 pb-2">
          <Button
            variant="primary"
            className="w-full justify-start"
            icon={<Plus className="h-4 w-4" strokeWidth={2.2} />}
            onClick={() => {
              onClose();
              navigate('/app/transactions/new');
            }}
          >
            New transaction
          </Button>
        </div>
        <NavList onNavigate={onClose} />
        <SystemStatus />
        <SidebarFooter dark={dark} onToggleTheme={onToggleTheme} onRefresh={onRefresh} />
      </div>
    </div>
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
    <header className="sticky top-0 z-10 border-b border-border bg-canvas/90 px-5 py-5 backdrop-blur-sm sm:px-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {breadcrumb && (
            <div className="mb-1.5 flex items-center gap-1.5 text-[0.8125rem] text-ink-3">{breadcrumb}</div>
          )}
          <h1 className="truncate text-[1.75rem] font-semibold leading-tight tracking-tight text-ink">
            {title}
          </h1>
          {description && <p className="mt-1 text-[0.9375rem] text-ink-2">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { dark, toggle } = useTheme();
  const queryClient = useQueryClient();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();

  // Close the drawer automatically whenever the route changes underneath it.
  useEffect(() => setDrawerOpen(false), [location.pathname]);

  const refresh = () => queryClient.invalidateQueries();

  return (
    <div className="min-h-screen bg-canvas">
      <Sidebar dark={dark} onToggleTheme={toggle} onRefresh={refresh} />
      <MobileTopBar onOpenMenu={() => setDrawerOpen(true)} dark={dark} onToggleTheme={toggle} />
      <MobileDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        dark={dark}
        onToggleTheme={toggle}
        onRefresh={refresh}
      />

      <div className="pt-14 lg:pl-64 lg:pt-0">{children}</div>
    </div>
  );
}
