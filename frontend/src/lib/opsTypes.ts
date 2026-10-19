/** Types for the operations layer: auth, roles, maker-checker, alerts, dashboard. */

import type { AuditEvent, EntryDirection, Money } from './types';

export type UserRole = 'OWNER' | 'ADMIN' | 'FINANCE' | 'OPERATIONS' | 'AUDITOR';

/** Mirrors `app.core.permissions.Permission` - the server is the source of truth. */
export type Permission =
  | 'accounts:read'
  | 'accounts:write'
  | 'transactions:read'
  | 'transactions:post'
  | 'drafts:read'
  | 'drafts:create'
  | 'drafts:approve'
  | 'investigations:read'
  | 'investigations:write'
  | 'reconciliation:read'
  | 'reconciliation:run'
  | 'audit:read'
  | 'alerts:read'
  | 'system:read'
  | 'dashboard:read'
  | 'users:manage'
  | 'api_keys:manage'
  | 'org:manage'
  | 'ai:use';

export interface CurrentUser {
  id: string;
  kind: 'user' | 'apikey';
  email: string | null;
  name: string;
  role: UserRole;
  permissions: Permission[];
  organization_id: string;
  organization_name: string | null;
  /** Scenario key when this organization is a fictional demonstration, else null. */
  organization_demo_scenario?: string | null;
  csrf_token: string | null;
}

export interface LoginResponse {
  /** For API clients; the console relies on the httpOnly cookie and `csrf_token`. */
  access_token: string;
  token_type: string;
  expires_at: string;
  csrf_token: string;
  user: CurrentUser;
}

export interface AuthConfig {
  signup_enabled: boolean;
  signup_code_required: boolean;
}

export interface RegisterRequest {
  organization_name: string;
  base_currency: string;
  email: string;
  full_name: string;
  password: string;
  signup_code?: string | null;
}

export interface ManagedUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
  locked: boolean;
}

export interface ApiKeyRecord {
  id: string;
  label: string;
  role: UserRole;
  key_prefix: string | null;
  created_at: string;
  created_by: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  is_active: boolean;
}

export interface ApiKeyCreated extends ApiKeyRecord {
  /** The plaintext key - present only in the creation response, never again. */
  key: string;
}

export interface Organization {
  id: string;
  name: string;
  base_currency: string;
  approval_threshold_minor: number | null;
}

// --- maker-checker ----------------------------------------------------------

export type DraftStatus = 'DRAFT' | 'PENDING_APPROVAL' | 'POSTED' | 'REJECTED';

export interface DraftEntry {
  account_id: string;
  account_code: string | null;
  direction: EntryDirection;
  amount: Money;
  memo: string | null;
}

export interface TransactionDraft {
  id: string;
  seq: number;
  status: DraftStatus;
  description: string;
  currency: string;
  reference: string | null;
  external_reference: string | null;
  effective_at: string | null;
  entries: DraftEntry[];
  total: Money;
  created_by: string;
  created_at: string;
  submitted_at: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decision_note: string | null;
  last_error: string | null;
  posted_transaction_id: string | null;
  is_mine: boolean;
  can_approve: boolean;
}

// --- alerts -----------------------------------------------------------------------

export type AlertSeverity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW';

export interface Alert {
  id: string;
  rule: string;
  severity: AlertSeverity;
  title: string;
  detail: string;
  count: number;
  last_seen: string | null;
  link: string | null;
  evidence: Record<string, unknown>;
}

export interface AlertsResponse {
  generated_at: string;
  window_hours: number;
  counts: Record<AlertSeverity, number>;
  alerts: Alert[];
}

// --- statements ---------------------------------------------------------------------

export interface StatementRow {
  entry_id: string;
  seq: number;
  transaction_id: string;
  reference: string;
  description: string;
  transaction_status: string;
  transaction_kind: string;
  actor: string;
  posted_at: string;
  direction: EntryDirection;
  debit: Money | null;
  credit: Money | null;
  running_balance: Money;
  memo: string | null;
}

export interface Statement {
  account: {
    id: string;
    code: string;
    name: string;
    type: string;
    currency: string;
    status: string;
    normal_balance: string;
  };
  opening_balance: Money;
  closing_balance: Money;
  current_balance: Money;
  total_debits: Money;
  total_credits: Money;
  items: StatementRow[];
  total: number;
  limit: number;
  offset: number;
}

// --- dashboard ------------------------------------------------------------------------

export interface Dashboard {
  generated_at: string;
  account_count: number;
  active_account_count: number;
  transaction_count: number;
  transactions_last_24h: number;
  recent_transactions: {
    id: string;
    reference: string;
    description: string;
    amount: Money;
    status: string;
    kind: string;
    actor: string;
    posted_at: string;
  }[];
  daily_volume: { day: string; currency: string; transaction_count: number; posted_minor: number }[];
  pending_approvals: number;
  /** Posted value per currency over the last `volume_window_days` days (reversed transactions excluded). */
  volume_window_days: number;
  posted_volume: { currency: string; transaction_count: number; posted_minor: number }[];
  /** Counted from the audit trail, last 24 hours. */
  failed_operations_24h: { rejected_postings: number; refused_approvals: number; rejected_drafts: number };
  open_investigations: number;
  recent_investigations: {
    id: string;
    case_number: string;
    title: string;
    status: string;
    priority: string;
    created_at: string;
  }[];
  latest_reconciliation: {
    run_id: string;
    run_number: number;
    run_at: string;
    external_count: number;
    matched: number;
    mismatched: number;
    missing_internal: number;
    missing_external: number;
    ambiguous: number;
    exceptions: number;
  } | null;
  ledger_balanced: boolean;
  integrity_all_passed: boolean;
  integrity_checks_failed: string[];
  database: string;
  alert_counts: Record<AlertSeverity, number>;
  top_alerts: Alert[];
  recent_audit: AuditEvent[];
}

// --- system health -----------------------------------------------------------------------

export interface ComponentStatus {
  status: string;
  detail: string;
}

export interface SystemHealth {
  generated_at: string;
  api: ComponentStatus;
  database: ComponentStatus;
  database_latency_ms: number;
  schema_revision: string | null;
  ledger_integrity: ComponentStatus;
  integrity_checks: { name: string; passed: boolean; detail: string }[];
  reconciliation: ComponentStatus;
  latest_reconciliation_at: string | null;
  requests_by_status_class: Record<string, number>;
  recent_errors: {
    at: string;
    status_code: number;
    code: string;
    path: string;
    request_id: string | null;
  }[];
}

// --- onboarding ---------------------------------------------------------------------------

export interface OnboardingStep {
  key: string;
  title: string;
  description: string;
  done: boolean;
  action_path: string | null;
}

export interface Onboarding {
  organization_name: string | null;
  steps: OnboardingStep[];
  completed: number;
  total: number;
}

export interface ReconciliationSource {
  source_system: string;
  record_count: number;
  latest_transaction_at: string | null;
  last_imported_at: string | null;
}

/** Mirrors `can_manage_role` on the server: an OWNER manages every role, an ADMIN only roles below ADMIN. */
export function canManageRole(actor: UserRole | undefined, target: UserRole): boolean {
  if (actor === 'OWNER') return true;
  if (actor === 'ADMIN') return target === 'FINANCE' || target === 'OPERATIONS' || target === 'AUDITOR';
  return false;
}

export const ALL_ROLES: UserRole[] = ['OWNER', 'ADMIN', 'FINANCE', 'OPERATIONS', 'AUDITOR'];

// --- casework -------------------------------------------------------------------------

export interface CaseNote {
  id: string;
  author: string;
  body: string;
  created_at: string;
}

export type MatchDecisionKind = 'CONFIRMED_MATCH' | 'NOT_A_MATCH';

export interface MatchDecisionRecord {
  id: string;
  decision: MatchDecisionKind;
  internal_transaction_id: string | null;
  reason: string;
  decided_by: string;
  created_at: string;
}

export interface MatchSuggestion {
  transaction_id: string;
  reference: string;
  description: string;
  amount: Money;
  effective_at: string;
  score: number;
  reasons: string[];
  requires_review: boolean;
}

export interface RelatedRecords {
  reconciliation: {
    result_id: string;
    run_number: number;
    status: string;
    source_system: string | null;
    external_id: string | null;
    explanation: string;
  }[];
  investigations: { id: string; case_number: string; title: string; status: string; priority: string }[];
  decisions: MatchDecisionRecord[];
}

// --- AI copilot ---------------------------------------------------------------------------

export type CopilotTaskKind =
  | 'SUMMARIZE'
  | 'MISSING_EVIDENCE'
  | 'NEXT_STEPS'
  | 'DRAFT_RESOLUTION'
  | 'DRAFT_REPORT';

export interface CopilotAnswer {
  task: string;
  summary: string;
  verified_facts: { statement: string; source: string }[];
  hypotheses: { statement: string; basis: string | null }[];
  missing_evidence: string[];
  suggested_next_steps: string[];
  document: string | null;
  evidence_sufficient: boolean;
  sources: { id: string; label: string; link: string | null }[];
  ungrounded_claims_moved: number;
  requires_review: boolean;
  disclaimer: string;
}
