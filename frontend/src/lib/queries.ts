/** React Query hooks - one place where every server route is named. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { buildQuery, newIdempotencyKey, request, type QueryValue } from './api';
import type {
  Account,
  AccountBalance,
  AuditEvent,
  Currency,
  Health,
  IntegrityReport,
  LedgerEntry,
  Page,
  SystemStats,
  Transaction,
  TransactionCreate,
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
  for (const key of ['accounts', 'account', 'transactions', 'transaction', 'entries', 'stats', 'audit', 'integrity']) {
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
