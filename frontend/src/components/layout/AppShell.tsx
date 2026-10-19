/**
 * Console application shell: fixed sidebar, content header, content well.
 *
 * The navigation is permission-aware: entries a role cannot use are not
 * shown. That is a convenience only - every endpoint behind them is
 * authorized again by the server. The sidebar also carries a live system
 * status area (API reachability, database health, trial-balance state)
 * because an operator's first question about a ledger is always "is it
 * consistent right now?" - and every figure there is read from the API.
 */

import { useQueryClient } from '@tanstack/react-query';
import {
  Activity,
  ArrowLeftRight,
  BellRing,
  BookOpen,
  ClipboardCheck,
  Inbox,
  FlaskConical,
  GraduationCap,
  Info,
  KeyRound,
  LayoutGrid,
  LogOut,
  Menu,
  Moon,
  Plus,
  RefreshCw,
  Scale,
  ScrollText,
  Search,
  SearchCheck,
  Settings,
  Sparkles,
  Sun,
  Users,
  Wallet,
  X,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';

import { Wordmark } from '../brand/Wordmark';
import { useAuth } from '../../lib/auth';
import { cn } from '../../lib/cn';
import { useIsDemo } from '../../lib/demo';
import { formatInteger } from '../../lib/format';
import { useAlertCount, usePendingApprovalCount } from '../../lib/opsQueries';
import type { Permission } from '../../lib/opsTypes';
import { useHealth, useStats } from '../../lib/queries';
import { useTheme } from '../../lib/theme';
import { DemoBanner } from '../demo/DemoBanner';
import { Badge, Button, IconButton, StatusDot } from '../ui/primitives';

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
  /** Hidden unless the role holds this permission (the server still enforces it). */
  permission?: Permission;
  badge?: 'approvals' | 'alerts';
  /** Shown only inside a fictional demonstration organization. */
  demoOnly?: boolean;
}

const NAV_GROUPS: { heading: string | null; items: NavItem[] }[] = [
  {
    heading: null,
    items: [
      { to: '/app', label: 'Dashboard', icon: LayoutGrid, end: true, permission: 'dashboard:read' },
      { to: '/app/inbox', label: 'Operations inbox', icon: Inbox, permission: 'dashboard:read' },
    ],
  },
  {
    heading: 'Ledger',
    items: [
      { to: '/app/accounts', label: 'Accounts', icon: Wallet, permission: 'accounts:read' },
      {
        to: '/app/transactions',
        label: 'Transactions',
        icon: ArrowLeftRight,
        end: true,
        permission: 'transactions:read',
      },
      {
        to: '/app/approvals',
        label: 'Approvals',
        icon: ClipboardCheck,
        permission: 'drafts:read',
        badge: 'approvals',
      },
      { to: '/app/ledger', label: 'Ledger explorer', icon: BookOpen, permission: 'transactions:read' },
      { to: '/app/flight-recorder', label: 'Flight Recorder', icon: Search, permission: 'accounts:read' },
    ],
  },
  {
    heading: 'Operations',
    items: [
      {
        to: '/app/investigations',
        label: 'Investigations',
        icon: SearchCheck,
        permission: 'investigations:read',
      },
      {
        to: '/app/reconciliation',
        label: 'Reconciliation',
        icon: Scale,
        permission: 'reconciliation:read',
      },
      { to: '/app/alerts', label: 'Alerts', icon: BellRing, permission: 'alerts:read', badge: 'alerts' },
      { to: '/app/audit', label: 'Audit log', icon: ScrollText, permission: 'audit:read' },
      { to: '/app/system', label: 'System health', icon: Activity, permission: 'system:read' },
    ],
  },
  {
    heading: 'Learn',
    items: [
      {
        to: '/app/learn',
        label: 'Learning guide',
        icon: GraduationCap,
        permission: 'dashboard:read',
        demoOnly: true,
      },
    ],
  },
  {
    heading: 'Intelligence',
    items: [
      { to: '/app/intelligence', label: 'LEDGR Intelligence', icon: Sparkles, permission: 'ai:use' },
    ],
  },
  {
    heading: 'Administration',
    items: [
      { to: '/app/admin/users', label: 'Users & roles', icon: Users, permission: 'users:manage' },
      { to: '/app/admin/api-keys', label: 'API keys', icon: KeyRound, permission: 'api_keys:manage' },
      { to: '/app/admin/settings', label: 'Organization', icon: Settings, permission: 'org:manage' },
    ],
  },
  { heading: null, items: [{ to: '/app/about', label: 'About', icon: Info }] },
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
      detail:
        stats.isLoading ? 'Checking…' : balanced === undefined ? '—' : balanced ? 'Balanced' : 'Unbalanced',
    },
  ];

  return (
    <div className="border-t border-border px-4 py-3">
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

function NavBadge({ kind }: { kind: 'approvals' | 'alerts' }) {
  const { can } = useAuth();
  const approvals = usePendingApprovalCount(kind === 'approvals' && can('drafts:read'));
  const alerts = useAlertCount(kind === 'alerts' && can('alerts:read'));
  const count = kind === 'approvals' ? approvals.data : alerts.data;
  if (!count) return null;
  return (
    <span
      className={cn(
        'ml-auto min-w-5 rounded-full px-1.5 text-center text-2xs font-semibold tabular-nums',
        kind === 'alerts' ? 'bg-negative-soft text-negative' : 'bg-accent-soft text-accent-ink',
      )}
      aria-label={`${count} ${kind === 'alerts' ? 'active alerts' : 'awaiting approval'}`}
    >
      {count}
    </span>
  );
}

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useAuth();
  const isDemo = useIsDemo();
  return (
    <nav className="flex-1 overflow-y-auto px-3 py-2" aria-label="Main">
      {NAV_GROUPS.map((group, index) => {
        const items = group.items.filter(
          (item) => (!item.demoOnly || isDemo) && (!item.permission || can(item.permission)),
        );
        if (items.length === 0) return null;
        return (
          <div key={group.heading ?? `group-${index}`} className={cn(index > 0 && 'mt-3')}>
            {group.heading && (
              <p className="px-2.5 pb-1 text-2xs font-semibold uppercase tracking-[0.06em] text-ink-3">
                {group.heading}
              </p>
            )}
            <div className="space-y-0.5">
              {items.map(({ to, label, icon: Icon, end, badge }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={end ?? false}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-2.5 rounded-sm px-2.5 py-1.5 text-[0.875rem] transition-colors',
                      isActive
                        ? 'bg-accent-soft font-medium text-accent-ink'
                        : 'text-ink-2 hover:bg-surface-2 hover:text-ink',
                    )
                  }
                >
                  <Icon className="h-4 w-4 shrink-0" strokeWidth={1.7} />
                  {label}
                  {badge && <NavBadge kind={badge} />}
                </NavLink>
              ))}
            </div>
          </div>
        );
      })}
    </nav>
  );
}

/** "New transaction" is offered only to roles that can post or propose one. */
function NewTransactionButton({ onClick }: { onClick: () => void }) {
  const { can } = useAuth();
  if (!can('transactions:post') && !can('drafts:create')) return null;
  return (
    <div className="px-3 pb-2">
      <Button
        variant="primary"
        className="w-full justify-start"
        icon={<Plus className="h-4 w-4" strokeWidth={2.2} />}
        onClick={onClick}
      >
        New transaction
      </Button>
    </div>
  );
}

function UserCard({ onSignedOut }: { onSignedOut?: () => void }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;
  const initial = (user.name || '?').trim().charAt(0).toUpperCase();

  return (
    <div className="flex items-center gap-2.5 border-t border-border px-4 py-3">
      <span
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[0.8125rem] font-semibold text-accent-ink"
        aria-hidden
      >
        {initial}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[0.8125rem] font-medium text-ink" title={user.name}>
          {user.name}
        </p>
        <Badge tone="neutral" className="mt-0.5">
          {user.role}
        </Badge>
      </div>
      <IconButton
        title="Sign out"
        aria-label="Sign out"
        onClick={async () => {
          await logout();
          onSignedOut?.();
          navigate('/login', { replace: true });
        }}
      >
        <LogOut className="h-4 w-4" strokeWidth={1.8} />
      </IconButton>
    </div>
  );
}

/** A permanent marker under the logo: this is a fictional organization. */
function DemoTag() {
  const isDemo = useIsDemo();
  const { user } = useAuth();
  if (!isDemo) return null;
  return (
    <div className="mt-2 flex items-center gap-1.5 rounded-sm border border-warning/30 bg-warning-soft px-2 py-1 text-2xs font-semibold uppercase tracking-[0.05em] text-warning">
      <FlaskConical className="h-3 w-3 shrink-0" strokeWidth={2} aria-hidden />
      <span className="truncate" title={user?.organization_name ?? undefined}>
        Demo - fictional data
      </span>
    </div>
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
      <span className="num text-2xs text-ink-3">v1.1.0</span>
      <div className="flex items-center gap-0.5">
        <IconButton title="Refresh data" aria-label="Refresh data" onClick={onRefresh}>
          <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.8} />
        </IconButton>
        <IconButton
          title={dark ? 'Switch to light theme' : 'Switch to dark theme'}
          aria-pressed={dark}
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
        <DemoTag />
      </div>

      <NewTransactionButton onClick={() => navigate('/app/transactions/new')} />

      <NavList />
      <SystemStatus />
      <UserCard />
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
        <IconButton title="Open menu" aria-label="Open menu" onClick={onOpenMenu}>
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
        {dark ? (
          <Sun className="h-4 w-4" strokeWidth={1.8} />
        ) : (
          <Moon className="h-4 w-4" strokeWidth={1.8} />
        )}
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
    <div className="fixed inset-0 z-30 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
      <div className="fixed inset-0 bg-ink/30" onClick={onClose} aria-hidden />
      <div className="fixed inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col bg-surface shadow-xl">
        <div className="flex items-center justify-between px-4 py-4">
          <Wordmark />
          <IconButton title="Close menu" aria-label="Close menu" onClick={onClose}>
            <X className="h-5 w-5" strokeWidth={1.8} />
          </IconButton>
        </div>
        <NewTransactionButton
          onClick={() => {
            onClose();
            navigate('/app/transactions/new');
          }}
        />
        <NavList onNavigate={onClose} />
        <SystemStatus />
        <UserCard onSignedOut={onClose} />
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

      <main className="pt-14 lg:pl-64 lg:pt-0">
        <DemoBanner />
        {children}
      </main>
    </div>
  );
}
