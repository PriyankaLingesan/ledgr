/**
 * Approvals (maker-checker).
 *
 * A draft is a proposal: it moves no money and has no ledger entries. Approving
 * it asks the server to post it through the same posting engine every other
 * transaction uses - same balance checks, same locking, same audit trail.
 * The person who proposed a draft can never approve it; the server enforces
 * that, and this page simply doesn't offer the button.
 */

import { AlertTriangle } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { Amount, DirectionBadge } from '../components/ledger/atoms';
import { DraftStatusBadge } from '../components/ops/atoms';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { Modal } from '../components/ui/Modal';
import { Button, Field, Panel, Textarea } from '../components/ui/primitives';
import { EmptyState, ErrorState, Pagination, Table, TableScroller, TableSkeleton, THead } from '../components/ui/Table';
import { useAuth } from '../lib/auth';
import { cn } from '../lib/cn';
import { formatDateTime, formatRelative } from '../lib/format';
import { useApproveDraft, useDrafts, useRejectDraft, useSubmitDraft } from '../lib/opsQueries';
import type { DraftStatus, TransactionDraft } from '../lib/opsTypes';
import { ApiError } from '../lib/api';
import { useToast } from '../lib/toast';

const LIMIT = 25;

type Tab = 'pending' | 'mine' | 'history';

const TABS: { key: Tab; label: string }[] = [
  { key: 'pending', label: 'Awaiting approval' },
  { key: 'mine', label: 'My drafts' },
  { key: 'history', label: 'Decided' },
];

function DraftDetail({
  draft,
  onClose,
}: {
  draft: TransactionDraft;
  onClose: () => void;
}) {
  const { can } = useAuth();
  const toast = useToast();
  const approve = useApproveDraft();
  const reject = useRejectDraft();
  const submit = useSubmitDraft();

  const [confirmApprove, setConfirmApprove] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState('');
  const [failure, setFailure] = useState<string | null>(draft.last_error);

  const busy = approve.isPending || reject.isPending || submit.isPending;

  return (
    <>
      <Modal
        open
        onClose={onClose}
        width="max-w-2xl"
        title={`Draft #${draft.seq}`}
        description={`Proposed by ${draft.created_by} · ${formatDateTime(draft.created_at)}`}
        footer={
          <>
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
            {draft.status === 'DRAFT' && draft.is_mine && can('drafts:create') && (
              <Button
                variant="primary"
                disabled={busy}
                onClick={() =>
                  submit.mutate(draft.id, {
                    onSuccess: () => {
                      toast.success('Submitted for approval');
                      onClose();
                    },
                    onError: (error) => toast.apiError(error),
                  })
                }
              >
                Submit for approval
              </Button>
            )}
            {draft.status === 'PENDING_APPROVAL' && can('drafts:approve') && (
              <>
                <Button variant="danger" disabled={busy} onClick={() => setRejecting(true)}>
                  Reject
                </Button>
                <Button
                  variant="primary"
                  disabled={busy || !draft.can_approve}
                  title={draft.is_mine ? 'You proposed this draft, so someone else must approve it' : undefined}
                  onClick={() => setConfirmApprove(true)}
                >
                  Approve & post
                </Button>
              </>
            )}
          </>
        }
      >
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <DraftStatusBadge status={draft.status} />
            <span className="text-[0.9375rem] font-medium text-ink">{draft.description}</span>
          </div>

          {draft.is_mine && draft.status === 'PENDING_APPROVAL' && (
            <p className="rounded-sm border border-border bg-surface-2 px-3 py-2 text-[0.8125rem] text-ink-2">
              You proposed this draft, so it needs approval from someone else (maker-checker).
            </p>
          )}

          {failure && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-sm border border-negative/30 bg-negative-soft px-3 py-2.5 text-[0.8125rem] text-negative"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.9} />
              <span>
                The ledger refused the last approval attempt, so nothing was posted: {failure}
              </span>
            </div>
          )}

          <TableScroller className="rounded-sm border border-border">
            <Table>
              <THead>
                <th className="px-3 py-2 text-xs font-medium text-ink-3">Account</th>
                <th className="px-3 py-2 text-xs font-medium text-ink-3" />
                <th className="px-3 py-2 text-right text-xs font-medium text-ink-3">Amount</th>
              </THead>
              <tbody className="divide-y divide-border">
                {draft.entries.map((entry, index) => (
                  <tr key={index}>
                    <td className="num px-3 py-2 text-[0.8125rem] text-ink">
                      {entry.account_code ?? entry.account_id.slice(0, 8)}
                      {entry.memo && <p className="font-sans text-xs text-ink-3">{entry.memo}</p>}
                    </td>
                    <td className="px-3 py-2">
                      <DirectionBadge direction={entry.direction} />
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Amount money={entry.amount} showCurrency />
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </TableScroller>

          <dl className="grid grid-cols-2 gap-3 text-[0.8125rem]">
            <div>
              <dt className="text-ink-3">Total</dt>
              <dd className="mt-0.5">
                <Amount money={draft.total} showCurrency />
              </dd>
            </div>
            <div>
              <dt className="text-ink-3">Reference</dt>
              <dd className="num mt-0.5 text-ink">{draft.reference ?? 'assigned on posting'}</dd>
            </div>
            {draft.decided_at && (
              <div className="col-span-2">
                <dt className="text-ink-3">{draft.status === 'POSTED' ? 'Approved by' : 'Rejected by'}</dt>
                <dd className="mt-0.5 text-ink">
                  {draft.decided_by} · {formatDateTime(draft.decided_at)}
                  {draft.decision_note && <span className="block text-ink-2">“{draft.decision_note}”</span>}
                </dd>
              </div>
            )}
            {draft.posted_transaction_id && (
              <div className="col-span-2">
                <Link
                  to={`/app/transactions/${draft.posted_transaction_id}`}
                  className="font-medium text-accent-ink hover:underline"
                >
                  View the posted transaction
                </Link>
              </div>
            )}
          </dl>
        </div>
      </Modal>

      <ConfirmDialog
        open={confirmApprove}
        title="Approve and post this transaction?"
        description="This posts a real, immutable ledger transaction through the normal posting engine. It can only be undone by a reversal."
        confirmLabel="Approve & post"
        busy={approve.isPending}
        onClose={() => setConfirmApprove(false)}
        onConfirm={() =>
          approve.mutate(
            { id: draft.id },
            {
              onSuccess: () => {
                toast.success('Approved and posted', `Draft #${draft.seq} is now in the ledger.`);
                setConfirmApprove(false);
                onClose();
              },
              onError: (error) => {
                setConfirmApprove(false);
                if (error instanceof ApiError && error.code === 'self_approval_forbidden') {
                  toast.error('You cannot approve your own draft', 'Ask a colleague to approve it.');
                } else if (error instanceof ApiError && error.status === 422) {
                  // The ledger refused (e.g. insufficient funds); the draft stays pending.
                  setFailure(`${error.code}: ${error.message}`);
                  toast.error('The ledger refused this posting', error.message);
                } else {
                  toast.apiError(error);
                }
              },
            },
          )
        }
      />

      <ConfirmDialog
        open={rejecting}
        title="Reject this draft?"
        description="Nothing is posted. The person who proposed it will see your reason."
        tone="danger"
        confirmLabel="Reject draft"
        busy={reject.isPending}
        confirmDisabled={!note.trim()}
        onClose={() => setRejecting(false)}
        onConfirm={() =>
          reject.mutate(
            { id: draft.id, note: note.trim() },
            {
              onSuccess: () => {
                toast.success('Draft rejected');
                setRejecting(false);
                onClose();
              },
              onError: (error) => {
                toast.apiError(error);
                setRejecting(false);
              },
            },
          )
        }
      >
        <Field label="Reason" htmlFor="reject-note" required>
          <Textarea
            id="reject-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Wrong cost centre - please re-submit against EXP.INFRA.USD"
            autoFocus
          />
        </Field>
      </ConfirmDialog>
    </>
  );
}

export default function ApprovalsPage() {
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>(can('drafts:approve') ? 'pending' : 'mine');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<TransactionDraft | null>(null);

  const filters: Record<Tab, Record<string, string | number | boolean>> = {
    pending: { status: 'PENDING_APPROVAL' satisfies DraftStatus },
    mine: { mine: true },
    history: {},
  };
  const query = useDrafts({ ...filters[tab], limit: LIMIT, offset });

  const items =
    tab === 'history'
      ? (query.data?.items ?? []).filter((d) => d.status === 'POSTED' || d.status === 'REJECTED')
      : (query.data?.items ?? []);

  return (
    <>
      <PageHeader
        title="Approvals"
        description="Proposed transactions waiting for a second person (maker-checker)"
        actions={
          (can('drafts:create') || can('transactions:post')) && (
            <Link to="/app/transactions/new">
              <Button variant="primary">New transaction</Button>
            </Link>
          )
        }
      />

      <div className="px-5 py-6 sm:px-8">
        <Panel>
          <div role="tablist" aria-label="Approval queues" className="flex gap-1 border-b border-border px-3 pt-2">
            {TABS.map((t) => (
              <button
                key={t.key}
                role="tab"
                type="button"
                aria-selected={tab === t.key}
                onClick={() => {
                  setTab(t.key);
                  setOffset(0);
                }}
                className={cn(
                  '-mb-px border-b-2 px-3 py-2 text-[0.875rem] transition-colors',
                  tab === t.key
                    ? 'border-accent font-medium text-ink'
                    : 'border-transparent text-ink-2 hover:text-ink',
                )}
              >
                {t.label}
              </button>
            ))}
          </div>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} context="drafts" />
          ) : query.isLoading ? (
            <TableSkeleton rows={5} columns={5} />
          ) : items.length > 0 ? (
            <>
              <TableScroller>
                <Table>
                  <THead>
                    <th className="px-4 py-2 text-xs font-medium text-ink-3">#</th>
                    <th className="px-4 py-2 text-xs font-medium text-ink-3">Description</th>
                    <th className="px-4 py-2 text-right text-xs font-medium text-ink-3">Amount</th>
                    <th className="px-4 py-2 text-xs font-medium text-ink-3">Proposed by</th>
                    <th className="px-4 py-2 text-xs font-medium text-ink-3">Status</th>
                  </THead>
                  <tbody className="divide-y divide-border">
                    {items.map((draft) => (
                      <tr
                        key={draft.id}
                        tabIndex={0}
                        role="button"
                        aria-label={`Open draft ${draft.seq}`}
                        className="cursor-pointer hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none"
                        onClick={() => setSelected(draft)}
                        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setSelected(draft)}
                      >
                        <td className="num px-4 py-3 text-[0.8125rem] text-ink-2">{draft.seq}</td>
                        <td className="max-w-md px-4 py-3">
                          <p className="truncate text-[0.875rem] font-medium text-ink">{draft.description}</p>
                          <p className="text-xs text-ink-3">
                            {draft.submitted_at ? `submitted ${formatRelative(draft.submitted_at)}` : 'not submitted'}
                          </p>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Amount money={draft.total} showCurrency />
                        </td>
                        <td className="px-4 py-3 text-[0.8125rem] text-ink-2">
                          {draft.created_by} {draft.is_mine && <span className="text-ink-3">(you)</span>}
                        </td>
                        <td className="px-4 py-3">
                          <DraftStatusBadge status={draft.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </TableScroller>
              <Pagination
                total={query.data?.total ?? 0}
                limit={LIMIT}
                offset={offset}
                onChange={setOffset}
                unit="drafts"
              />
            </>
          ) : (
            <EmptyState
              title={
                tab === 'pending'
                  ? 'Nothing is waiting for approval'
                  : tab === 'mine'
                    ? 'You have not proposed any transactions'
                    : 'No decided drafts yet'
              }
              description={
                tab === 'pending'
                  ? 'When someone submits a draft for approval it will appear here.'
                  : 'Drafts you create appear here until they are approved or rejected.'
              }
            />
          )}
        </Panel>
      </div>

      {selected && <DraftDetail draft={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
