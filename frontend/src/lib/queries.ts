/** React Query hooks - one place where every server route is named. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { buildQuery, newIdempotencyKey, request, type QueryValue } from './api';
import type {
  Account,
  AccountBalance,
  AccountBalanceAt,
  AIStatus,
  AskLedgerAnswer,
  AuditEvent,
  BalanceChange,
  ControlException,
  CorrectionSimulation,
  Currency,
  DuplicateGroup,
  EntryInput,
  ExplainTransaction,
  Health,
  ImportResult,
  IntegrityChecklist,
  IntegrityReport,
  InvestigationCase,
  InvestigationCaseCreate,
  InvestigationCaseEvidence,
  InvestigationStatus,
  LedgerBrief,
  LedgerEntry,
  Page,
  ReconciliationRun,
  ReconciliationRunSummary,
  SystemStats,
  Transaction,
  TransactionCorrectionRequest,
  TransactionCorrectionResult,
  TransactionCreate,
  TransactionLineage,
  TransactionProposal,
} from './types';

export const keys = {
  health: ['health'] as const,
  currencies: ['currencies'] as const,
  stats: (days: number) => ['stats', days] as const,
  integrity: ['integrity'] as const,
  accounts: (params: Record<string, QueryValue>) => ['accounts', params] as const,
  account: (id: string) => ['account', id] as const,
  accountBalance: (id: string) => ['account', id, 'balance'] as const,
  accountEntries: (id: string, params: Record<string, QueryValue>) =>
    ['account', id, 'entries', params] as const,
  transactions: (params: Record<string, QueryValue>) => ['transactions', params] as const,
  transaction: (id: string) => ['transaction', id] as const,
  entries: (params: Record<string, QueryValue>) => ['entries', params] as const,
  audit: (params: Record<string, QueryValue>) => ['audit', params] as const,
  aiStatus: ['ai', 'status'] as const,
  ledgerBrief: (days: number) => ['ai', 'ledger-brief', days] as const,
  balanceAt: (id: string, at: string) => ['account', id, 'balance-at', at] as const,
  balanceChange: (id: string, params: Record<string, QueryValue>) =>
    ['account', id, 'balance-change', params] as const,
  duplicates: ['investigate', 'duplicates'] as const,
  integrityChecklist: ['investigate', 'integrity-checklist'] as const,
  lineage: (id: string) => ['transaction', id, 'lineage'] as const,
  controlExceptions: ['control-exceptions'] as const,
  investigations: (params: Record<string, QueryValue>) => ['investigations', params] as const,
  investigation: (id: string) => ['investigation', id] as const,
  investigationEvidence: (id: string) => ['investigation', id, 'evidence'] as const,
  reconciliationRuns: (params: Record<string, QueryValue>) => ['reconciliation-runs', params] as const,
  reconciliationRun: (id: string) => ['reconciliation-run', id] as const,
};

export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    queryFn: () => request<Health>('/system/health'),
    refetchInterval: 30_000,
    retry: false,
  });
}

export function useCurrencies() {
  return useQuery({
    queryKey: keys.currencies,
    queryFn: () => request<Currency[]>('/system/currencies'),
    // The ISO registry does not change while the console is open.
    staleTime: Infinity,
  });
}

export function useStats(days = 14) {
  return useQuery({
    queryKey: keys.stats(days),
    queryFn: () => request<SystemStats>(`/system/stats${buildQuery({ days })}`),
  });
}

export function useIntegrity(enabled = false) {
  return useQuery({
    queryKey: keys.integrity,
    queryFn: () => request<IntegrityReport>('/system/integrity'),
    enabled,
  });
}

export function useAccounts(params: Record<string, QueryValue>) {
  return useQuery({
    queryKey: keys.accounts(params),
    queryFn: () => request<Page<Account>>(`/accounts${buildQuery(params)}`),
    placeholderData: (previous) => previous,
  });
}

export function useAccount(id: string | undefined) {
  return useQuery({
    queryKey: keys.account(id ?? ''),
    queryFn: () => request<Account>(`/accounts/${id}`),
    enabled: Boolean(id),
  });
}

export function useAccountBalance(id: string | undefined) {
  return useQuery({
    queryKey: keys.accountBalance(id ?? ''),
    queryFn: () => request<AccountBalance>(`/accounts/${id}/balance`),
    enabled: Boolean(id),
  });
}

export function useAccountEntries(id: string | undefined, params: Record<string, QueryValue>) {
  return useQuery({
    queryKey: keys.accountEntries(id ?? '', params),
    queryFn: () => request<Page<LedgerEntry>>(`/accounts/${id}/entries${buildQuery(params)}`),
    enabled: Boolean(id),
    placeholderData: (previous) => previous,
  });
}

export function useAccountTransactions(id: string | undefined, params: Record<string, QueryValue>) {
  return useQuery({
    queryKey: ['account', id ?? '', 'transactions', params],
    queryFn: () => request<Page<Transaction>>(`/accounts/${id}/transactions${buildQuery(params)}`),
    enabled: Boolean(id),
    placeholderData: (previous) => previous,
  });
}

export function useTransactions(params: Record<string, QueryValue>) {
  return useQuery({
    queryKey: keys.transactions(params),
    queryFn: () => request<Page<Transaction>>(`/transactions${buildQuery(params)}`),
    placeholderData: (previous) => previous,
  });
}

export function useTransaction(id: string | undefined) {
  return useQuery({
    queryKey: keys.transaction(id ?? ''),
    queryFn: () => request<Transaction>(`/transactions/${id}`),
    enabled: Boolean(id),
  });
}

export function useEntries(params: Record<string, QueryValue>) {
  return useQuery({
    queryKey: keys.entries(params),
    queryFn: () => request<Page<LedgerEntry>>(`/ledger/entries${buildQuery(params)}`),
    placeholderData: (previous) => previous,
  });
}

export function useAuditEvents(params: Record<string, QueryValue>, enabled = true) {
  return useQuery({
    queryKey: keys.audit(params),
    queryFn: () => request<Page<AuditEvent>>(`/system/audit${buildQuery(params)}`),
    enabled,
  });
}

function invalidateLedger(client: ReturnType<typeof useQueryClient>) {
  for (const key of [
    'accounts',
    'account',
    'transactions',
    'transaction',
    'entries',
    'stats',
    'audit',
    'integrity',
    'dashboard',
    'statement',
    'alerts',
    'onboarding',
    'drafts',
  ]) {
    client.invalidateQueries({ queryKey: [key] });
  }
}

export function useCreateTransaction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: TransactionCreate & { idempotencyKey?: string }) => {
      const { idempotencyKey, ...body } = payload;
      return request<Transaction>('/transactions', {
        method: 'POST',
        body,
        headers: { 'Idempotency-Key': idempotencyKey ?? newIdempotencyKey() },
      });
    },
    onSuccess: () => invalidateLedger(client),
  });
}

export function useReverseTransaction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      request<Transaction>(`/transactions/${id}/reverse`, {
        method: 'POST',
        body: { reason: reason || null },
        headers: { 'Idempotency-Key': newIdempotencyKey() },
      }),
    onSuccess: () => invalidateLedger(client),
  });
}

export function useCreateAccount() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      code: string;
      name: string;
      type: string;
      currency: string;
      description?: string | null;
      allows_negative_balance?: boolean;
    }) => request<Account>('/accounts', { method: 'POST', body }),
    onSuccess: () => invalidateLedger(client),
  });
}

// --- LEDGR Intelligence ----------------------------------------------------
//
// Every AI feature here is read-only or proposal-only from the frontend's
// point of view: none of these hooks post a transaction. Reviewing and
// posting an AI proposal is still `useCreateTransaction()` above, called
// with the proposal's own `post_body` - the same mutation, same endpoint,
// same idempotency handling as a hand-typed transaction.

export function useAIStatus() {
  return useQuery({
    queryKey: keys.aiStatus,
    queryFn: () => request<AIStatus>('/ai/status'),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** Real facts always; AI phrasing only when configured - so this never errors
 *  the way the other AI hooks can, and is safe to keep a long staleTime on:
 *  the "Refresh" action on the widget is what re-triggers the LLM call. */
export function useLedgerBrief(days = 1) {
  return useQuery({
    queryKey: keys.ledgerBrief(days),
    queryFn: () => request<LedgerBrief>(`/ai/ledger-brief${buildQuery({ days })}`),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useProposeTransaction() {
  return useMutation({
    mutationFn: (description: string) =>
      request<TransactionProposal>('/ai/transactions/propose', {
        method: 'POST',
        body: { description },
      }),
  });
}

export function useAskLedger() {
  return useMutation({
    mutationFn: (question: string) =>
      request<AskLedgerAnswer>('/ai/ask', { method: 'POST', body: { question } }),
  });
}

/** A GET on the backend, wrapped as a mutation here: it's an on-demand
 *  operator action ("Explain with AI"), not data the page should fetch on
 *  its own or silently refetch - the same reasoning as the ledger brief's
 *  explicit refresh button, applied to a button that runs once per click. */
export function useExplainTransaction() {
  return useMutation({
    mutationFn: (transactionId: string) =>
      request<ExplainTransaction>(`/ai/transactions/${transactionId}/explain`),
  });
}

// --- Financial Flight Recorder ----------------------------------------------
//
// Every hook here is read-only except `useCorrectTransaction`, which - like
// `useReverseTransaction` - never edits a posted transaction; it calls the
// existing reverse-then-repost service and returns the three transactions
// involved. Nothing here can write a ledger entry on its own.

export function useAccountBalanceAt(id: string | undefined, at: string | undefined) {
  return useQuery({
    queryKey: keys.balanceAt(id ?? '', at ?? ''),
    queryFn: () => request<AccountBalanceAt>(`/accounts/${id}/balance-at${buildQuery({ at })}`),
    enabled: Boolean(id) && Boolean(at),
  });
}

export function useAccountBalanceChange(
  id: string | undefined,
  params: { from?: string; to?: string } = {},
) {
  return useQuery({
    queryKey: keys.balanceChange(id ?? '', params),
    queryFn: () => request<BalanceChange>(`/accounts/${id}/balance-change${buildQuery(params)}`),
    enabled: Boolean(id),
  });
}

export function useDuplicates(enabled = true) {
  return useQuery({
    queryKey: keys.duplicates,
    queryFn: () => request<DuplicateGroup[]>('/system/duplicates'),
    enabled,
  });
}

export function useIntegrityChecklist(enabled = true) {
  return useQuery({
    queryKey: keys.integrityChecklist,
    queryFn: () => request<IntegrityChecklist>('/system/integrity-checklist'),
    enabled,
  });
}

export function useCorrectTransaction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: TransactionCorrectionRequest & { id: string }) =>
      request<TransactionCorrectionResult>(`/transactions/${id}/correct`, {
        method: 'POST',
        body,
        // Same protection as post/reverse: a double-click or a network retry
        // replays the original result instead of correcting a transaction twice.
        headers: { 'Idempotency-Key': newIdempotencyKey() },
      }),
    onSuccess: () => invalidateLedger(client),
  });
}

// --- transaction lineage -----------------------------------------------

export function useTransactionLineage(id: string | undefined) {
  return useQuery({
    queryKey: keys.lineage(id ?? ''),
    queryFn: () => request<TransactionLineage>(`/transactions/${id}/lineage`),
    enabled: Boolean(id),
  });
}

// --- what-if correction simulator ---------------------------------------
//
// A mutation, not a query: simulating is an on-demand "preview impact"
// action the operator triggers explicitly, the same reasoning as
// `useExplainTransaction`. It issues a real HTTP request but the handler
// behind it never commits anything - discarding the result changes nothing.

export function useSimulateCorrection() {
  return useMutation({
    mutationFn: ({ id, entries }: { id: string; entries: EntryInput[] }) =>
      request<CorrectionSimulation>(`/transactions/${id}/simulate-correction`, {
        method: 'POST',
        body: { entries },
      }),
  });
}

// --- control exception engine --------------------------------------------

export function useControlExceptions(enabled = true) {
  return useQuery({
    queryKey: keys.controlExceptions,
    queryFn: () => request<ControlException[]>('/system/control-exceptions'),
    enabled,
  });
}

// --- investigation cases --------------------------------------------------

export function useInvestigations(params: Record<string, QueryValue> = {}) {
  return useQuery({
    queryKey: keys.investigations(params),
    queryFn: () => request<Page<InvestigationCase>>(`/investigations${buildQuery(params)}`),
    placeholderData: (previous) => previous,
  });
}

export function useInvestigation(id: string | undefined) {
  return useQuery({
    queryKey: keys.investigation(id ?? ''),
    queryFn: () => request<InvestigationCase>(`/investigations/${id}`),
    enabled: Boolean(id),
  });
}

export function useInvestigationEvidence(id: string | undefined) {
  return useQuery({
    queryKey: keys.investigationEvidence(id ?? ''),
    queryFn: () => request<InvestigationCaseEvidence>(`/investigations/${id}/evidence`),
    enabled: Boolean(id),
  });
}

function invalidateInvestigations(client: ReturnType<typeof useQueryClient>) {
  client.invalidateQueries({ queryKey: ['investigations'] });
  client.invalidateQueries({ queryKey: ['investigation'] });
  client.invalidateQueries({ queryKey: ['dashboard'] });
  client.invalidateQueries({ queryKey: ['audit'] });
  client.invalidateQueries({ queryKey: ['reconciliation-run'] });
}

export function useCreateInvestigation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: InvestigationCaseCreate) =>
      request<InvestigationCase>('/investigations', { method: 'POST', body }),
    onSuccess: () => invalidateInvestigations(client),
  });
}

export function useUpdateInvestigationStatus() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: InvestigationStatus }) =>
      request<InvestigationCase>(`/investigations/${id}/status`, {
        method: 'PATCH',
        body: { status },
      }),
    onSuccess: () => invalidateInvestigations(client),
  });
}

// --- external reconciliation ----------------------------------------------

export function useReconciliationRuns(params: Record<string, QueryValue> = {}) {
  return useQuery({
    queryKey: keys.reconciliationRuns(params),
    queryFn: () => request<Page<ReconciliationRunSummary>>(`/reconciliation/runs${buildQuery(params)}`),
    placeholderData: (previous) => previous,
  });
}

export function useReconciliationRun(id: string | undefined) {
  return useQuery({
    queryKey: keys.reconciliationRun(id ?? ''),
    queryFn: () => request<ReconciliationRun>(`/reconciliation/runs/${id}`),
    enabled: Boolean(id),
  });
}

function invalidateReconciliation(client: ReturnType<typeof useQueryClient>) {
  client.invalidateQueries({ queryKey: ['reconciliation-runs'] });
  client.invalidateQueries({ queryKey: ['reconciliation-run'] });
  for (const key of ['dashboard', 'alerts', 'audit', 'onboarding', 'reconciliation-sources']) {
    client.invalidateQueries({ queryKey: [key] });
  }
}

export function useImportDemoReconciliationData() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => request<ImportResult>('/reconciliation/import-demo', { method: 'POST' }),
    onSuccess: () => invalidateReconciliation(client),
  });
}

export function useRunReconciliation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => request<ReconciliationRun>('/reconciliation/run', { method: 'POST' }),
    onSuccess: () => invalidateReconciliation(client),
  });
}

export function useInvestigateReconciliationResult() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (resultId: string) =>
      request<InvestigationCase>(`/reconciliation/results/${resultId}/investigate`, {
        method: 'POST',
      }),
    onSuccess: () => invalidateInvestigations(client),
  });
}

export function useResolveInvestigation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      resolution_note,
      correction_transaction_id,
    }: {
      id: string;
      resolution_note: string;
      correction_transaction_id?: string | null;
    }) =>
      request<InvestigationCase>(`/investigations/${id}/resolve`, {
        method: 'POST',
        body: { resolution_note, correction_transaction_id: correction_transaction_id ?? null },
      }),
    onSuccess: () => invalidateInvestigations(client),
  });
}
