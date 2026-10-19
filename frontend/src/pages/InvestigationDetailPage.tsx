/**
 * Financial Incident Investigator.
 *
 * Evidence assembled here is never fabricated: opening/closing/net balance,
 * related transactions and duplicate candidates, ledger-wide integrity, and
 * (when the case names a triggering transaction) its full lineage all come
 * straight from `app.services.investigation`, the same engine behind the
 * Flight Recorder and Control Exception Engine. This page only decides which
 * of those views apply to a given case and lays out the story: Detection ->
 * Investigation -> Resolution.
 */

import {
  CheckCircle2,
  ChevronRight,
  Download,
  Loader2,
  Printer,
  SearchCheck,
  ShieldAlert,
  XCircle,
} from 'lucide-react';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { CaseCopilot } from '../components/ai/CopilotPanel';
import { BalanceChangePanel } from '../components/investigation/BalanceChangePanel';
import { NotesPanel } from '../components/investigation/NotesPanel';
import { TransactionLineagePanel } from '../components/investigation/TransactionLineagePanel';
import { PageHeader } from '../components/layout/AppShell';
import { AccountRef, Amount, DirectionBadge } from '../components/ledger/atoms';
import { AuditActionBadge, CaseStatusBadge, PriorityBadge, ReconStatusBadge } from '../components/ops/atoms';
import { Modal } from '../components/ui/Modal';
import { Badge, Button, Field, Panel, PanelHeader, Skeleton, Textarea } from '../components/ui/primitives';
import { EmptyState, ErrorState } from '../components/ui/Table';
import { downloadFile } from '../lib/api';
import { useAuth } from '../lib/auth';
import { formatDateTime } from '../lib/format';
import { useToast } from '../lib/toast';
import {
  useInvestigation,
  useInvestigationEvidence,
  useResolveInvestigation,
  useUpdateInvestigationStatus,
} from '../lib/queries';

function ResolveDialog({
  caseId,
  open,
  onClose,
}: {
  caseId: string;
  open: boolean;
  onClose: () => void;
}) {
  const [note, setNote] = useState('');
  const resolve = useResolveInvestigation();
  const toast = useToast();

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Resolve investigation"
      description="Records what was found and closes the case. It can be reopened later if needed."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={resolve.isPending || !note.trim()}
            icon={resolve.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : undefined}
            onClick={() =>
              resolve.mutate(
                { id: caseId, resolution_note: note.trim() },
                {
                  onSuccess: () => {
                    toast.success('Investigation resolved');
                    onClose();
                    setNote('');
                  },
                  onError: (error) => toast.apiError(error),
                },
              )
            }
          >
            Resolve case
          </Button>
        </>
      }
    >
      <Field label="Resolution" htmlFor="resolution-note" required hint="What did you find, and what (if anything) was done about it?">
        <Textarea
          id="resolution-note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Confirmed as a timing difference between two related postings - no correction needed."
        />
      </Field>
    </Modal>
  );
}

export default function InvestigationDetailPage() {
  const { caseId } = useParams();
  const [resolveOpen, setResolveOpen] = useState(false);

  const caseQuery = useInvestigation(caseId);
  const evidence = useInvestigationEvidence(caseId);
  const updateStatus = useUpdateInvestigationStatus();
  const { can } = useAuth();
  const toast = useToast();
  const canWrite = can('investigations:write');
  const changeStatus = (id: string, status: 'INVESTIGATING') =>
    updateStatus.mutate(
      { id, status },
      {
        onSuccess: () => toast.success('Status updated'),
        onError: (error) => toast.apiError(error),
      },
    );

  if (caseQuery.isError) {
    return (
      <>
        <PageHeader title="Investigation" />
        <div className="px-5 py-6 sm:px-8">
          <Panel>
            <ErrorState error={caseQuery.error} onRetry={() => caseQuery.refetch()} />
          </Panel>
        </div>
      </>
    );
  }

  const investigationCase = caseQuery.data;
  const canStartInvestigating = investigationCase?.status === 'OPEN';
  const canResolve = investigationCase?.status !== 'RESOLVED';
  const canReopen = investigationCase?.status === 'RESOLVED';

  return (
    <>
      <PageHeader
        breadcrumb={
          <>
            <Link to="/app/investigations" className="hover:text-ink">
              Investigations
            </Link>
            <ChevronRight className="h-3 w-3" />
            <span className="num">{investigationCase?.case_number ?? '…'}</span>
          </>
        }
        title={investigationCase?.title ?? 'Loading investigation…'}
        description={investigationCase?.issue_summary}
        actions={
          investigationCase && (
            <div className="flex flex-wrap items-center gap-2 print:hidden">
              <PriorityBadge priority={investigationCase.priority} />
              <CaseStatusBadge status={investigationCase.status} />
              {canWrite && canStartInvestigating && (
                <Button
                  disabled={updateStatus.isPending}
                  onClick={() => changeStatus(investigationCase.id, 'INVESTIGATING')}
                >
                  Start investigating
                </Button>
              )}
              {canWrite && canReopen && (
                <Button
                  disabled={updateStatus.isPending}
                  onClick={() => changeStatus(investigationCase.id, 'INVESTIGATING')}
                >
                  Reopen
                </Button>
              )}
              {canWrite && canResolve && (
                <Button
                  variant="primary"
                  icon={<CheckCircle2 className="h-3.5 w-3.5" />}
                  onClick={() => setResolveOpen(true)}
                >
                  Resolve
                </Button>
              )}
              <Button
                icon={<Download className="h-3.5 w-3.5" />}
                onClick={() =>
                  downloadFile(`/investigations/${investigationCase.id}/report`, `${investigationCase.case_number}-evidence.md`).catch(
                    (error) => toast.apiError(error),
                  )
                }
              >
                Download evidence
              </Button>
              <Button icon={<Printer className="h-3.5 w-3.5" />} onClick={() => window.print()}>
                Print
              </Button>
            </div>
          )
        }
      />

      <div className="space-y-4 px-5 py-6 sm:px-8">
        {!investigationCase ? (
          <>
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-64 w-full" />
          </>
        ) : (
          <>
            <Panel>
              <PanelHeader title="Case record" />
              <dl className="grid grid-cols-1 divide-y divide-border sm:grid-cols-2 sm:divide-x sm:divide-y-0">
                <div className="divide-y divide-border">
                  <div className="flex items-center justify-between px-4 py-2.5">
                    <dt className="text-[0.8125rem] text-ink-3">Source</dt>
                    <dd className="text-[0.8125rem] text-ink-2">{investigationCase.source}</dd>
                  </div>
                  <div className="flex items-center justify-between px-4 py-2.5">
                    <dt className="text-[0.8125rem] text-ink-3">Account</dt>
                    <dd className="text-[0.8125rem] text-ink-2">
                      {investigationCase.account ? (
                        <AccountRef account={investigationCase.account} />
                      ) : (
                        <span className="text-ink-3">Ledger-wide</span>
                      )}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between px-4 py-2.5">
                    <dt className="text-[0.8125rem] text-ink-3">Created by</dt>
                    <dd className="num text-[0.8125rem] text-ink-2">{investigationCase.created_by}</dd>
                  </div>
                  <div className="flex items-center justify-between px-4 py-2.5">
                    <dt className="text-[0.8125rem] text-ink-3">Opened</dt>
                    <dd className="num text-[0.8125rem] text-ink-2">
                      {formatDateTime(investigationCase.created_at)}
                    </dd>
                  </div>
                </div>
                <div className="divide-y divide-border">
                  <div className="flex items-center justify-between px-4 py-2.5">
                    <dt className="text-[0.8125rem] text-ink-3">Resolved</dt>
                    <dd className="num text-[0.8125rem] text-ink-2">
                      {formatDateTime(investigationCase.resolved_at)}
                    </dd>
                  </div>
                  {investigationCase.correction_transaction_id && (
                    <div className="flex items-center justify-between px-4 py-2.5">
                      <dt className="text-[0.8125rem] text-ink-3">Correction applied</dt>
                      <dd className="text-[0.8125rem]">
                        <Link
                          to={`/app/transactions/${investigationCase.correction_transaction_id}`}
                          className="num text-accent-ink hover:underline"
                        >
                          View correction
                        </Link>
                      </dd>
                    </div>
                  )}
                  {investigationCase.resolution_note && (
                    <div className="px-4 py-2.5">
                      <dt className="text-[0.8125rem] text-ink-3">Resolution</dt>
                      <dd className="mt-1 text-[0.8125rem] text-ink-2">
                        {investigationCase.resolution_note}
                      </dd>
                    </div>
                  )}
                </div>
              </dl>
            </Panel>

            <div className="grid gap-4 xl:grid-cols-2">
              <NotesPanel caseId={investigationCase.id} />
              <CaseCopilot caseId={investigationCase.id} />
            </div>

            {evidence.isError ? (
              <Panel>
                <ErrorState error={evidence.error} context="the evidence" onRetry={() => evidence.refetch()} />
              </Panel>
            ) : evidence.isLoading || !evidence.data ? (
              <Skeleton className="h-48 w-full" />
            ) : (
              <>
                {evidence.data.reconciliation && (
                  <Panel>
                    <PanelHeader
                      title="Reconciliation evidence"
                      description="The exception this case was opened from. Read-only - investigating never changes the ledger or this result."
                      actions={<ReconStatusBadge status={evidence.data.reconciliation.status} />}
                    />
                    <div className="grid gap-px bg-border sm:grid-cols-3">
                      <div className="bg-surface px-4 py-3">
                        <p className="caption">Internal amount (ledger)</p>
                        <p className="mt-1">
                          <Amount money={evidence.data.reconciliation.internal_amount} showCurrency />
                        </p>
                        {evidence.data.reconciliation.internal_reference && (
                          <p className="num mt-0.5 text-xs text-ink-3">
                            {evidence.data.reconciliation.internal_reference}
                          </p>
                        )}
                      </div>
                      <div className="bg-surface px-4 py-3">
                        <p className="caption">External amount</p>
                        <p className="mt-1">
                          <Amount money={evidence.data.reconciliation.external_transaction?.amount} showCurrency />
                        </p>
                        {evidence.data.reconciliation.external_transaction && (
                          <p className="num mt-0.5 text-xs text-ink-3">
                            {evidence.data.reconciliation.external_transaction.source_system} ·{' '}
                            {evidence.data.reconciliation.external_transaction.external_id}
                          </p>
                        )}
                      </div>
                      <div className="bg-surface px-4 py-3">
                        <p className="caption">Difference</p>
                        <p className="mt-1">
                          <Amount money={evidence.data.reconciliation.difference} showCurrency tone="auto" />
                        </p>
                      </div>
                    </div>
                    <p className="border-t border-border px-4 py-3 text-[0.8125rem] text-ink-2">
                      {evidence.data.reconciliation.explanation}
                    </p>
                  </Panel>
                )}

                {evidence.data.balance_change && (
                  <Panel>
                    <PanelHeader
                      title="Balance change over the case window"
                      description="Opening balance, every related transaction, and the resulting balance."
                    />
                    <BalanceChangePanel change={evidence.data.balance_change} isLoading={false} showPlayback={false} />
                  </Panel>
                )}

                {evidence.data.lineage && (
                  <TransactionLineagePanel transactionId={evidence.data.lineage.root.id} />
                )}

                <Panel>
                  <PanelHeader
                    title="Potential duplicates"
                    description="Structurally similar postings near the case account - a prompt to look closer, not a verdict."
                    actions={
                      evidence.data.duplicate_candidates.length > 0 && (
                        <Badge tone="warning">{evidence.data.duplicate_candidates.length}</Badge>
                      )
                    }
                  />
                  {evidence.data.duplicate_candidates.length === 0 ? (
                    <EmptyState
                      icon={<SearchCheck className="h-4 w-4" strokeWidth={1.6} />}
                      title="No matching pairs found"
                    />
                  ) : (
                    <ul className="divide-y divide-border">
                      {evidence.data.duplicate_candidates.map((group, index) => (
                        <li key={index} className="px-4 py-3">
                          <div className="flex flex-wrap items-center gap-2">
                            <Badge tone="warning">Possible duplicate</Badge>
                            <span className="text-[0.8125rem] text-ink-3">
                              {group.reasons.join(' · ')}
                            </span>
                          </div>
                          <div className="mt-2 space-y-1.5">
                            {group.entries.map((entry) => (
                              <Link
                                key={entry.transaction_id}
                                to={`/app/transactions/${entry.transaction_id}`}
                                className="row-link flex items-center justify-between gap-3 rounded-sm border border-border px-3 py-2"
                              >
                                <div className="min-w-0">
                                  <span className="num text-[0.8125rem] font-medium text-ink">
                                    {entry.reference}
                                  </span>
                                  <span className="ml-2 text-[0.8125rem] text-ink-3">
                                    {entry.account_code}
                                  </span>
                                </div>
                                <div className="flex shrink-0 items-center gap-2">
                                  <DirectionBadge direction={entry.direction} />
                                  <Amount money={entry.amount} showCurrency />
                                </div>
                              </Link>
                            ))}
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>

                <Panel>
                  <PanelHeader
                    title="Ledger-wide integrity"
                    description="Every correctness check LEDGR enforces, re-verified against the real data."
                    actions={
                      <Badge tone={evidence.data.integrity.every((c) => c.passed) ? 'positive' : 'negative'}>
                        {evidence.data.integrity.every((c) => c.passed) ? 'All passing' : 'Attention needed'}
                      </Badge>
                    }
                  />
                  <ul className="divide-y divide-border">
                    {evidence.data.integrity.map((check) => (
                      <li key={check.name} className="flex items-start gap-2.5 px-4 py-2.5">
                        {check.passed ? (
                          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-positive" strokeWidth={1.9} />
                        ) : (
                          <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-negative" strokeWidth={1.9} />
                        )}
                        <div className="min-w-0">
                          <p className="text-[0.875rem] text-ink">{check.name}</p>
                          <p className="mt-0.5 text-[0.8125rem] text-ink-3">{check.detail}</p>
                        </div>
                      </li>
                    ))}
                  </ul>
                </Panel>

                <Panel>
                  <PanelHeader
                    title="Audit trail"
                    description="Real audit events for this case and its triggering transaction"
                  />
                  {evidence.data.audit_events.length === 0 ? (
                    <EmptyState title="No audit events recorded" />
                  ) : (
                    <ul className="divide-y divide-border">
                      {evidence.data.audit_events.map((event) => (
                        <li key={event.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5">
                          <div className="flex items-center gap-2">
                            <AuditActionBadge eventType={event.event_type} />
                            <span className="text-[0.8125rem] text-ink-2">{event.actor}</span>
                          </div>
                          <span className="num text-xs text-ink-3">{formatDateTime(event.created_at)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>

                <p className="flex items-center gap-2 text-2xs text-ink-3">
                  <ShieldAlert className="h-3 w-3" strokeWidth={1.8} />
                  Original record preserved: every transaction shown above remains exactly as posted;
                  a correction, if any, is a separate linked transaction - never an edit.
                </p>
              </>
            )}
          </>
        )}
      </div>

      {investigationCase && (
        <ResolveDialog caseId={investigationCase.id} open={resolveOpen} onClose={() => setResolveOpen(false)} />
      )}
    </>
  );
}
