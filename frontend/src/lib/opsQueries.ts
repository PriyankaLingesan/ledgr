/** React Query hooks for the operations layer (admin, approvals, alerts, dashboard...). */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { buildQuery, request, type QueryValue } from './api';
import type {
  Alert,
  AlertsResponse,
  ApiKeyCreated,
  CaseNote,
  CopilotAnswer,
  CopilotTaskKind,
  MatchDecisionRecord,
  MatchSuggestion,
  RelatedRecords,
  ApiKeyRecord,
  Dashboard,
  DraftStatus,
  ManagedUser,
  Onboarding,
  Organization,
  ReconciliationSource,
  Statement,
  SystemHealth,
  TransactionDraft,
  UserRole,
} from './opsTypes';
import type { ImportResult, Page, TransactionCreate } from './types';

function invalidateLedgerViews(client: ReturnType<typeof useQueryClient>) {
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
    'drafts',
    'statement',
    'alerts',
    'onboarding',
  ]) {
    client.invalidateQueries({ queryKey: [key] });
  }
}

// --- dashboard, alerts, health, onboarding -----------------------------------

export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: () => request<Dashboard>('/dashboard'),
    refetchInterval: 60_000,
  });
}

export function useAlerts() {
  return useQuery({
    queryKey: ['alerts'],
    queryFn: () => request<AlertsResponse>('/alerts'),
    refetchInterval: 60_000,
  });
}

/** Just the severity counts for a nav badge; `enabled` keeps it off for roles without access. */
export function useAlertCount(enabled: boolean) {
  return useQuery({
    queryKey: ['alerts'],
    queryFn: () => request<AlertsResponse>('/alerts'),
    refetchInterval: 60_000,
    enabled,
    select: (data) => data.counts.CRITICAL + data.counts.HIGH + data.counts.MEDIUM + data.counts.LOW,
  });
}

export function useSystemHealth() {
  return useQuery({
    queryKey: ['system-health'],
    queryFn: () => request<SystemHealth>('/system/health/detail'),
    refetchInterval: 30_000,
  });
}

export function useOnboarding() {
  return useQuery({
    queryKey: ['onboarding'],
    queryFn: () => request<Onboarding>('/onboarding'),
  });
}

export function useLoadDemoData() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      request<{ message: string; created: string[] }>('/onboarding/demo-data', { method: 'POST' }),
    onSuccess: () => {
      invalidateLedgerViews(client);
      client.invalidateQueries({ queryKey: ['reconciliation-runs'] });
      client.invalidateQueries({ queryKey: ['reconciliation-sources'] });
    },
  });
}

// --- statements -----------------------------------------------------------------

export function useStatement(id: string | undefined, params: Record<string, QueryValue>) {
  return useQuery({
    queryKey: ['statement', id ?? '', params],
    queryFn: () => request<Statement>(`/accounts/${id}/statement${buildQuery(params)}`),
    enabled: Boolean(id),
    placeholderData: (previous) => previous,
  });
}

// --- maker-checker ------------------------------------------------------------------

export function useDrafts(params: Record<string, QueryValue>) {
  return useQuery({
    queryKey: ['drafts', params],
    queryFn: () => request<Page<TransactionDraft>>(`/transaction-drafts${buildQuery(params)}`),
    placeholderData: (previous) => previous,
  });
}

export function usePendingApprovalCount(enabled: boolean) {
  return useQuery({
    queryKey: ['drafts', { status: 'PENDING_APPROVAL' satisfies DraftStatus, limit: 1 }],
    queryFn: () =>
      request<Page<TransactionDraft>>(
        `/transaction-drafts${buildQuery({ status: 'PENDING_APPROVAL', limit: 1 })}`,
      ),
    enabled,
    refetchInterval: 60_000,
    select: (page) => page.total,
  });
}

export function useCreateDraft() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ submit, ...body }: TransactionCreate & { submit: boolean }) =>
      request<TransactionDraft>(`/transaction-drafts${buildQuery({ submit })}`, {
        method: 'POST',
        body,
      }),
    onSuccess: () => invalidateLedgerViews(client),
  });
}

export function useSubmitDraft() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      request<TransactionDraft>(`/transaction-drafts/${id}/submit`, { method: 'POST' }),
    onSuccess: () => invalidateLedgerViews(client),
  });
}

export function useApproveDraft() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note?: string }) =>
      request<TransactionDraft>(`/transaction-drafts/${id}/approve`, {
        method: 'POST',
        body: { note: note || null },
      }),
    // Approval is a real posting: every ledger-derived view is now stale.
    onSuccess: () => invalidateLedgerViews(client),
    onError: () => client.invalidateQueries({ queryKey: ['drafts'] }),
  });
}

export function useRejectDraft() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      request<TransactionDraft>(`/transaction-drafts/${id}/reject`, {
        method: 'POST',
        body: { note },
      }),
    onSuccess: () => invalidateLedgerViews(client),
  });
}

// --- administration -------------------------------------------------------------------

export function useUsers() {
  return useQuery({ queryKey: ['users'], queryFn: () => request<ManagedUser[]>('/users') });
}

export function useCreateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; full_name: string; password: string; role: UserRole }) =>
      request<ManagedUser>('/users', { method: 'POST', body }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['users'] }),
  });
}

export function useUpdateUser() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      ...body
    }: {
      id: string;
      role?: UserRole;
      is_active?: boolean;
      full_name?: string;
    }) => request<ManagedUser>(`/users/${id}`, { method: 'PATCH', body }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['users'] }),
  });
}

export function useResetPassword() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, new_password }: { id: string; new_password: string }) =>
      request<ManagedUser>(`/users/${id}/reset-password`, { method: 'POST', body: { new_password } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['users'] }),
  });
}

export function useApiKeys() {
  return useQuery({ queryKey: ['api-keys'], queryFn: () => request<ApiKeyRecord[]>('/api-keys') });
}

export function useCreateApiKey() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: { label: string; role: UserRole }) =>
      request<ApiKeyCreated>('/api-keys', { method: 'POST', body }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['api-keys'] }),
  });
}

export function useRevokeApiKey() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => request<ApiKeyRecord>(`/api-keys/${id}/revoke`, { method: 'POST' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['api-keys'] }),
  });
}

export function useOrganization() {
  return useQuery({
    queryKey: ['organization'],
    queryFn: () => request<Organization>('/organization'),
  });
}

export function useUpdateOrganization() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name?: string;
      approval_threshold_minor?: number;
      clear_approval_threshold?: boolean;
    }) => request<Organization>('/organization', { method: 'PATCH', body }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['organization'] });
      client.invalidateQueries({ queryKey: ['onboarding'] });
    },
  });
}

// --- reconciliation workspace ----------------------------------------------------------

export function useReconciliationSources() {
  return useQuery({
    queryKey: ['reconciliation-sources'],
    queryFn: () => request<ReconciliationSource[]>('/reconciliation/sources'),
  });
}

export function useAuditEventTypes() {
  return useQuery({
    queryKey: ['audit-event-types'],
    queryFn: () => request<string[]>('/system/audit/event-types'),
    staleTime: 60_000,
  });
}

export function useImportExternalRecords() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      source_system: string;
      records: {
        external_id: string;
        amount_minor: number;
        currency: string;
        direction: 'DEBIT' | 'CREDIT';
        transaction_timestamp: string;
        description?: string;
      }[];
    }) => request<ImportResult>('/reconciliation/import', { method: 'POST', body }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['reconciliation-sources'] });
      client.invalidateQueries({ queryKey: ['onboarding'] });
    },
  });
}

export type { Alert };

// --- casework: notes, suggestions, decisions, related ------------------------------------------

export function useCaseNotes(caseId: string | undefined) {
  return useQuery({
    queryKey: ['case-notes', caseId ?? ''],
    queryFn: () => request<CaseNote[]>(`/investigations/${caseId}/notes`),
    enabled: Boolean(caseId),
  });
}

export function useAddCaseNote() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ caseId, body }: { caseId: string; body: string }) =>
      request<CaseNote>(`/investigations/${caseId}/notes`, { method: 'POST', body: { body } }),
    onSuccess: (_note, vars) => {
      client.invalidateQueries({ queryKey: ['case-notes', vars.caseId] });
      client.invalidateQueries({ queryKey: ['investigation', vars.caseId, 'evidence'] });
      client.invalidateQueries({ queryKey: ['audit'] });
    },
  });
}

export function useMatchSuggestions(resultId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ['match-suggestions', resultId ?? ''],
    queryFn: () => request<MatchSuggestion[]>(`/reconciliation/results/${resultId}/suggestions`),
    enabled: Boolean(resultId) && enabled,
  });
}

export function useRecordDecision() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({
      resultId,
      ...body
    }: {
      resultId: string;
      decision: 'CONFIRMED_MATCH' | 'NOT_A_MATCH';
      internal_transaction_id?: string | null;
      reason: string;
    }) =>
      request<MatchDecisionRecord>(`/reconciliation/results/${resultId}/decisions`, {
        method: 'POST',
        body,
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['reconciliation-run'] });
      client.invalidateQueries({ queryKey: ['audit'] });
    },
  });
}

export function useRelatedRecords(transactionId: string | undefined) {
  return useQuery({
    queryKey: ['transaction', transactionId ?? '', 'related'],
    queryFn: () => request<RelatedRecords>(`/transactions/${transactionId}/related`),
    enabled: Boolean(transactionId),
  });
}

// --- AI copilot (drafts for review; nothing here changes any record) --------------------------------

export function useCaseCopilot() {
  return useMutation({
    mutationFn: ({ caseId, task }: { caseId: string; task: CopilotTaskKind }) =>
      request<CopilotAnswer>(`/ai/investigations/${caseId}/assist`, { method: 'POST', body: { task } }),
  });
}

export function useExplainResult() {
  return useMutation({
    mutationFn: (resultId: string) =>
      request<CopilotAnswer>(`/ai/reconciliation/results/${resultId}/explain`, { method: 'POST' }),
  });
}

export function useTransactionHistoryAI() {
  return useMutation({
    mutationFn: (transactionId: string) =>
      request<CopilotAnswer>(`/ai/transactions/${transactionId}/history`, { method: 'POST' }),
  });
}
