/** Small display atoms shared by the operations screens. */

import { Check, Circle } from 'lucide-react';
import { Link } from 'react-router-dom';

import { cn } from '../../lib/cn';
import type { AlertSeverity, DraftStatus, Onboarding, UserRole } from '../../lib/opsTypes';
import type { InvestigationPriority, InvestigationStatus, ReconciliationStatus } from '../../lib/types';
import { Badge, Panel, PanelHeader } from '../ui/primitives';

type Tone = 'neutral' | 'accent' | 'positive' | 'negative' | 'warning' | 'debit' | 'credit';

export function RoleBadge({ role }: { role: UserRole }) {
  const tone: Record<UserRole, Tone> = {
    OWNER: 'accent',
    ADMIN: 'accent',
    FINANCE: 'debit',
    OPERATIONS: 'positive',
    AUDITOR: 'neutral',
  };
  return <Badge tone={tone[role]}>{role}</Badge>;
}

export const ROLE_DESCRIPTIONS: Record<UserRole, string> = {
  OWNER: 'Created the organization. Everything, including granting any role; always at least one.',
  ADMIN:
    'Everything operationally, including users, API keys and settings - but can only grant or change roles below ADMIN.',
  FINANCE: 'Creates, posts and approves transactions; reconciles; manages accounts.',
  OPERATIONS: 'Investigates exceptions and runs reconciliation. Cannot move money.',
  AUDITOR: 'Read-only access to the ledger, investigations, reconciliation and audit log.',
};

export function SeverityBadge({ severity }: { severity: AlertSeverity }) {
  const tone: Record<AlertSeverity, Tone> = {
    CRITICAL: 'negative',
    HIGH: 'negative',
    MEDIUM: 'warning',
    LOW: 'neutral',
  };
  return <Badge tone={tone[severity]}>{severity}</Badge>;
}

export function PriorityBadge({ priority }: { priority: InvestigationPriority | string }) {
  const tone: Record<string, Tone> = {
    CRITICAL: 'negative',
    HIGH: 'negative',
    MEDIUM: 'warning',
    LOW: 'neutral',
  };
  return <Badge tone={tone[priority] ?? 'neutral'}>{priority}</Badge>;
}

export function CaseStatusBadge({ status }: { status: InvestigationStatus | string }) {
  const tone: Record<string, Tone> = { OPEN: 'warning', INVESTIGATING: 'accent', RESOLVED: 'positive' };
  return <Badge tone={tone[status] ?? 'neutral'}>{status}</Badge>;
}

export function DraftStatusBadge({ status }: { status: DraftStatus }) {
  const tone: Record<DraftStatus, Tone> = {
    DRAFT: 'neutral',
    PENDING_APPROVAL: 'warning',
    POSTED: 'positive',
    REJECTED: 'negative',
  };
  return <Badge tone={tone[status]}>{status === 'PENDING_APPROVAL' ? 'Pending approval' : status}</Badge>;
}

export const RECON_LABELS: Record<ReconciliationStatus, string> = {
  MATCHED: 'Matched',
  AMOUNT_MISMATCH: 'Amount mismatch',
  MISSING_INTERNAL: 'Missing in ledger',
  MISSING_EXTERNAL: 'Missing externally',
  AMBIGUOUS_INTERNAL_MATCH: 'Ambiguous',
};

export function ReconStatusBadge({ status }: { status: ReconciliationStatus }) {
  const tone: Record<ReconciliationStatus, Tone> = {
    MATCHED: 'positive',
    AMOUNT_MISMATCH: 'negative',
    MISSING_INTERNAL: 'warning',
    MISSING_EXTERNAL: 'warning',
    AMBIGUOUS_INTERNAL_MATCH: 'accent',
  };
  return <Badge tone={tone[status]}>{RECON_LABELS[status]}</Badge>;
}

/** Human labels for the audit trail's event types. Unknown types fall back to the raw value. */
const AUDIT_LABELS: Record<string, string> = {
  'auth.login': 'Signed in',
  'auth.logout': 'Signed out',
  'auth.login_failed': 'Failed sign-in',
  'auth.permission_denied': 'Permission denied',
  'setup.completed': 'Organization set up',
  'organization.registered': 'Organization registered',
  'investigation.note_added': 'Investigator note added',
  'reconciliation.decision_recorded': 'Reviewer decision recorded',
  'export.transactions': 'Transactions exported',
  'ai.copilot_used': 'AI copilot used',
  'user.created': 'User created',
  'user.updated': 'User updated',
  'user.password_reset': 'Password reset',
  'user.password_changed': 'Password changed',
  'organization.updated': 'Organization updated',
  'api_key.created': 'API key created',
  'api_key.revoked': 'API key revoked',
  'account.created': 'Account created',
  'account.updated': 'Account updated',
  'transaction.posted': 'Transaction posted',
  'transaction.reversed': 'Transaction reversed',
  'transaction.post_failed': 'Posting rejected',
  'idempotency.replay': 'Idempotent retry replayed',
  'idempotency.rejected': 'Idempotency key refused',
  'draft.created': 'Draft created',
  'draft.updated': 'Draft edited',
  'draft.submitted': 'Draft submitted',
  'draft.approved': 'Draft approved',
  'draft.rejected': 'Draft rejected',
  'draft.approval_failed': 'Approval refused by ledger',
  'draft.self_approval_blocked': 'Self-approval blocked',
  'investigation.created': 'Investigation opened',
  'investigation.status_changed': 'Investigation status changed',
  'investigation.resolved': 'Investigation resolved',
  'reconciliation.run_completed': 'Reconciliation run',
  'reconciliation.imported': 'External records imported',
  'demo.loaded': 'Demo data loaded',
};

export function auditLabel(eventType: string): string {
  return AUDIT_LABELS[eventType] ?? eventType;
}

export function AuditActionBadge({ eventType }: { eventType: string }) {
  let tone: Tone = 'neutral';
  if (/failed|denied|blocked|rejected|revoked/.test(eventType)) tone = 'negative';
  else if (/approved|posted|resolved|login$|created|completed/.test(eventType)) tone = 'positive';
  else if (/reversed|replay|status_changed|updated|reset/.test(eventType)) tone = 'warning';
  return <Badge tone={tone}>{auditLabel(eventType)}</Badge>;
}

/** First-run guidance computed from real state by the server (`GET /onboarding`). */
export function OnboardingChecklist({ onboarding }: { onboarding: Onboarding }) {
  return (
    <Panel>
      <PanelHeader
        title="Getting started"
        description={`${onboarding.completed} of ${onboarding.total} steps complete`}
      />
      <ol className="divide-y divide-border">
        {onboarding.steps.map((step) => (
          <li key={step.key} className="flex items-start gap-3 px-4 py-3">
            <span
              className={cn(
                'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border',
                step.done
                  ? 'border-positive/40 bg-positive-soft text-positive'
                  : 'border-border-strong text-ink-3',
              )}
              aria-hidden
            >
              {step.done ? (
                <Check className="h-3 w-3" strokeWidth={2.5} />
              ) : (
                <Circle className="h-2 w-2" strokeWidth={0} fill="currentColor" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <p className={cn('text-[0.875rem] font-medium', step.done ? 'text-ink-3 line-through' : 'text-ink')}>
                {step.title}
              </p>
              <p className="text-[0.8125rem] text-ink-2">{step.description}</p>
            </div>
            {!step.done && step.action_path && (
              <Link
                to={`/app${step.action_path}`}
                className="shrink-0 text-[0.8125rem] font-medium text-accent-ink hover:underline"
              >
                Go
              </Link>
            )}
          </li>
        ))}
      </ol>
    </Panel>
  );
}
