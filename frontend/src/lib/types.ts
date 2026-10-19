/** Mirrors the FastAPI response models exactly. */

export type AccountType = 'ASSET' | 'LIABILITY' | 'EQUITY' | 'REVENUE' | 'EXPENSE';
export type AccountStatus = 'ACTIVE' | 'FROZEN' | 'CLOSED';
export type NormalBalance = 'DEBIT' | 'CREDIT';
export type EntryDirection = 'DEBIT' | 'CREDIT';
export type TransactionStatus = 'POSTED' | 'REVERSED';
export type TransactionKind = 'STANDARD' | 'REVERSAL';

export interface Money {
  amount_minor: number;
  currency: string;
  /** Major units, exact, rendered by the server. */
  amount: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface AccountSummary {
  id: string;
  code: string;
  name: string;
  type: AccountType;
  normal_balance: NormalBalance;
  currency: string;
  status: AccountStatus;
}

export interface Account extends AccountSummary {
  description: string | null;
  allows_negative_balance: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  balance: Money;
  debits: Money;
  credits: Money;
  entry_count: number;
}

export interface AccountBalance {
  account_id: string;
  account_code: string;
  currency: string;
  normal_balance: NormalBalance;
  balance: Money;
  signed_balance: Money;
  debits: Money;
  credits: Money;
  entry_count: number;
  last_entry_seq: number | null;
  derived_at: string;
  cache: {
    balance: Money;
    debits: Money;
    credits: Money;
    entry_count: number;
    last_entry_seq: number | null;
    updated_at: string | null;
  };
  cache_consistent: boolean;
}

export interface TransactionSummary {
  id: string;
  reference: string;
  description: string;
  status: TransactionStatus;
  kind: TransactionKind;
  currency: string;
  posted_at: string;
  effective_at: string;
}

export interface LedgerEntry {
  id: string;
  seq: number;
  transaction_id: string;
  account_id: string;
  direction: EntryDirection;
  amount: Money;
  entry_index: number;
  memo: string | null;
  created_at: string;
  account: AccountSummary | null;
  transaction: TransactionSummary | null;
}

export interface Transaction {
  id: string;
  seq: number;
  reference: string;
  description: string;
  currency: string;
  status: TransactionStatus;
  kind: TransactionKind;
  total_debits: Money;
  total_credits: Money;
  balanced: boolean;
  entry_count: number;
  effective_at: string;
  posted_at: string;
  reversed_at: string | null;
  reverses_transaction_id: string | null;
  reversed_by_transaction_id: string | null;
  actor: string;
  request_id: string | null;
  idempotency_key: string | null;
  external_reference: string | null;
  metadata: Record<string, unknown>;
  entries: LedgerEntry[];
}

export interface Currency {
  code: string;
  name: string;
  exponent: number;
}

export interface TrialBalanceRow {
  currency: string;
  debits: Money;
  credits: Money;
  difference: Money;
  balanced: boolean;
  entry_count: number;
}

export interface SystemStats {
  generated_at: string;
  account_count: number;
  active_account_count: number;
  accounts_by_type: { type: AccountType; count: number }[];
  transaction_count: number;
  reversed_transaction_count: number;
  entry_count: number;
  trial_balance: TrialBalanceRow[];
  ledger_balanced: boolean;
  daily_volume: {
    day: string;
    currency: string;
    transaction_count: number;
    posted: Money;
  }[];
}

export interface IntegrityReport {
  checked_at: string;
  accounts_checked: number;
  cache_consistent: boolean;
  trial_balance_balanced: boolean;
  unbalanced_transaction_ids: string[];
  issues: {
    account_id: string;
    account_code: string;
    field: string;
    cached: number;
    derived: number;
  }[];
}

export interface AuditEvent {
  id: string;
  seq: number;
  event_type: string;
  resource_type: string;
  resource_id: string | null;
  actor: string;
  request_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Health {
  status: string;
  service: string;
  version: string;
  environment: string;
  database: string;
}

export interface EntryInput {
  account_id: string;
  direction: EntryDirection;
  amount_minor: number;
  memo?: string | null;
}

export interface TransactionCreate {
  description: string;
  currency: string;
  entries: EntryInput[];
  reference?: string | null;
  effective_at?: string | null;
  external_reference?: string | null;
  metadata?: Record<string, unknown>;
}

// --- LEDGR Intelligence ----------------------------------------------------
//
// An AI-generated *proposal* is never a transaction: it has no id, no seq,
// no posted_at, because nothing has been written yet. `post_body` is the
// exact body the existing `POST /transactions` expects - reviewing and
// posting a proposal means sending `post_body` there, unmodified except for
// whatever the operator chose to edit first.

export interface AIStatus {
  available: boolean;
  provider: string | null;
  reason: string | null;
}

export interface ProposedEntry {
  account_id: string | null;
  account_code: string;
  account_name: string | null;
  direction: EntryDirection;
  amount: Money;
}

export interface TransactionProposal {
  valid: boolean;
  issues: string[];
  description: string;
  currency: string;
  entries: ProposedEntry[];
  total_debits: Money | null;
  total_credits: Money | null;
  post_body: TransactionCreate | null;
}

export interface AskLedgerAnswer {
  answer: string;
  tools_used: string[];
}

export interface ExplainTransaction {
  transaction_id: string;
  reference: string;
  explanation: string;
}

export interface CurrencyActivity {
  currency: string;
  transaction_count: number;
  total_value: Money;
  largest_transaction_reference: string | null;
  largest_amount: Money;
}

export interface LedgerBriefStats {
  window_days: number;
  total_transactions: number;
  all_transactions_balanced: boolean;
  by_currency: CurrencyActivity[];
}

export interface LedgerBrief {
  generated_at: string;
  is_ai_generated: boolean;
  summary: string;
  stats: LedgerBriefStats;
}

// --- Financial Flight Recorder ---------------------------------------------
//
// Every figure here is derived from ledger entries at read time - the same
// principle as the rest of the API. `BalanceChange` is the one shape behind
// "Explain This Balance", the timeline, state comparison, and "why did my
// balance change": they are the same query with different `from`/`to`.

export interface AccountBalanceAt {
  account_id: string;
  account_code: string;
  account_name: string;
  currency: string;
  normal_balance: NormalBalance;
  as_of: string;
  balance: Money;
  debits: Money;
  credits: Money;
  entry_count: number;
}

export interface InvestigationEvent {
  entry_id: string;
  seq: number;
  transaction_id: string;
  reference: string;
  description: string;
  direction: EntryDirection;
  amount: Money;
  /** Signed, in the account's own normal orientation. */
  effect: Money;
  running_balance: Money;
  created_at: string;
  transaction_status: TransactionStatus;
  transaction_kind: TransactionKind;
  reverses_transaction_id: string | null;
  reversed_by_transaction_id: string | null;
}

export interface BalanceChange {
  account_id: string;
  account_code: string;
  account_name: string;
  currency: string;
  normal_balance: NormalBalance;
  from: string;
  to: string;
  opening_balance: Money;
  closing_balance: Money;
  net_change: Money;
  event_count: number;
  events: InvestigationEvent[];
  /** True unless the two independent computations of this window disagree. */
  reconciled: boolean;
}

export interface DuplicateEntry {
  transaction_id: string;
  reference: string;
  description: string;
  account_code: string;
  direction: EntryDirection;
  amount: Money;
  created_at: string;
}

export interface DuplicateGroup {
  label: string;
  reasons: string[];
  entries: DuplicateEntry[];
}

export interface IntegrityCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface IntegrityChecklist {
  checked_at: string;
  all_passed: boolean;
  checks: IntegrityCheck[];
}

export interface TransactionCorrectionRequest {
  entries: EntryInput[];
  reason: string;
  description?: string | null;
  reference?: string | null;
}

export interface TransactionCorrectionResult {
  original_id: string;
  original_reference: string;
  reversal_id: string;
  reversal_reference: string;
  corrected_id: string;
  corrected_reference: string;
}

// --- transaction lineage -----------------------------------------------
//
// Every edge here is a real relationship already on the schema (the
// `reverses_transaction_id` foreign key) or the correction metadata
// `correct_transaction` already writes - never inferred or guessed.

export interface TransactionNode {
  id: string;
  reference: string;
  description: string;
  status: TransactionStatus;
  kind: TransactionKind;
  currency: string;
  total_debits: Money;
  total_credits: Money;
  posted_at: string;
}

export interface TransactionLineage {
  root: TransactionNode;
  accounts: AccountSummary[];
  reverses: TransactionNode | null;
  reversed_by: TransactionNode | null;
  corrects: TransactionNode | null;
  corrected_by: TransactionNode | null;
}

// --- what-if correction simulator ---------------------------------------
//
// Read-only: simulating never writes to the ledger. Discarding the result
// is a no-op; only POST .../correct changes anything.

export interface SimulatedAccountImpact {
  account_id: string;
  account_code: string;
  account_name: string;
  currency: string;
  current_balance: Money;
  simulated_balance: Money;
  delta: Money;
}

export interface CorrectionSimulation {
  original_id: string;
  original_reference: string;
  impacts: SimulatedAccountImpact[];
}

// --- control exception engine --------------------------------------------
//
// Every exception is a real, already-detected signal - never a verdict, and
// never labelled fraud.

export type ControlExceptionKind = 'POTENTIAL_DUPLICATE' | 'INTEGRITY';

export interface ControlException {
  id: string;
  kind: ControlExceptionKind;
  title: string;
  detail: string;
  account_id: string | null;
  transaction_ids: string[];
  detected_at: string;
}

// --- investigation cases --------------------------------------------------
//
// The operational record of "someone is looking into this". Carries no
// money of its own - every figure in a case's evidence bundle is derived
// live from the ledger by the same functions the Flight Recorder uses.

export type InvestigationStatus = 'OPEN' | 'INVESTIGATING' | 'RESOLVED';
export type InvestigationSource =
  | 'MANUAL'
  | 'DUPLICATE'
  | 'INTEGRITY'
  | 'FLIGHT_RECORDER'
  | 'RECONCILIATION';
export type InvestigationPriority = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export interface InvestigationCase {
  id: string;
  case_number: string;
  title: string;
  issue_summary: string;
  status: InvestigationStatus;
  source: InvestigationSource;
  source_reference: string | null;
  priority: InvestigationPriority;
  reconciliation_result_id: string | null;
  account: AccountSummary | null;
  window_from: string | null;
  window_to: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  correction_transaction_id: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface InvestigationCaseCreate {
  title: string;
  issue_summary: string;
  source?: InvestigationSource;
  source_reference?: string | null;
  priority?: InvestigationPriority;
  account_id?: string | null;
  window_from?: string | null;
  window_to?: string | null;
}

export interface InvestigationCaseEvidence {
  case: InvestigationCase;
  balance_change: BalanceChange | null;
  duplicate_candidates: DuplicateGroup[];
  integrity: IntegrityCheck[];
  lineage: TransactionLineage | null;
  /** The reconciliation exception this case was opened from, if any. */
  reconciliation: ReconciliationResult | null;
  audit_events: AuditEvent[];
}

// --- external reconciliation ----------------------------------------------
//
// Deterministic comparison of external evidence against the internal
// ledger. Read-only with respect to the ledger: nothing here posts,
// reverses, or corrects - only the existing transaction endpoints do that.

export type ReconciliationStatus =
  | 'MATCHED'
  | 'AMOUNT_MISMATCH'
  | 'MISSING_INTERNAL'
  | 'MISSING_EXTERNAL'
  | 'AMBIGUOUS_INTERNAL_MATCH';

export interface ExternalTransaction {
  id: string;
  source_system: string;
  external_id: string;
  account_reference: string | null;
  amount: Money;
  direction: EntryDirection;
  status: string;
  transaction_timestamp: string;
  description: string;
}

export interface ReconciliationResult {
  id: string;
  status: ReconciliationStatus;
  explanation: string;
  difference: Money | null;
  /** null only for MISSING_EXTERNAL: a live internal transaction with no external record. */
  external_transaction: ExternalTransaction | null;
  internal_transaction_id: string | null;
  internal_reference: string | null;
  internal_amount: Money | null;
  investigation_id: string | null;
  investigation_case_number: string | null;
  latest_decision: {
    id: string;
    decision: 'CONFIRMED_MATCH' | 'NOT_A_MATCH';
    internal_transaction_id: string | null;
    reason: string;
    decided_by: string;
    created_at: string;
  } | null;
}

export interface ReconciliationRunSummary {
  id: string;
  run_number: number;
  created_at: string;
  external_count: number;
  matched_count: number;
  mismatch_count: number;
  missing_internal_count: number;
  missing_external_count: number;
  ambiguous_count: number;
}

export interface ReconciliationRun extends ReconciliationRunSummary {
  results: ReconciliationResult[];
}

export interface ImportResult {
  imported: number;
  skipped_duplicates: number;
}
