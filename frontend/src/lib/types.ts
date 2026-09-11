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
