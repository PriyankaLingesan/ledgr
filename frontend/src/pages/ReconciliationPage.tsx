/**
 * Reconciliation workspace.
 *
 * External evidence (a bank or payment processor's own records) is compared
 * against the ledger deterministically - same external reference, same
 * amount, same currency. No AI, no similarity scores, no guessing.
 *
 * This screen is strictly read-only with respect to the ledger: it can import
 * evidence and run a comparison, but nothing here posts, reverses or "fixes"
 * a transaction to force a match. An ambiguous match stays ambiguous until a
 * person decides what is true, and the only route to changing financial
 * state is the existing, audited correction workflow via an investigation.
 */

import { Loader2, PlayCircle, Search, SearchCheck, UploadCloud } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { Amount } from '../components/ledger/atoms';
import { RECON_LABELS, ReconStatusBadge } from '../components/ops/atoms';
import { ExceptionReview } from '../components/reconciliation/ExceptionReview';
import { ImportWizard } from '../components/reconciliation/ImportWizard';
import { Modal } from '../components/ui/Modal';
import { Badge, Button, Input, Panel, PanelHeader, Select, Skeleton } from '../components/ui/primitives';
import { EmptyState, ErrorState, Table, TableScroller, THead } from '../components/ui/Table';
import { useAuth } from '../lib/auth';
import { cn } from '../lib/cn';
import { formatDateTime, formatRelative } from '../lib/format';
import { useLoadDemoData, useReconciliationSources } from '../lib/opsQueries';
import {
  useInvestigateReconciliationResult,
  useReconciliationRun,
  useReconciliationRuns,
  useRunReconciliation,
} from '../lib/queries';
import { useToast } from '../lib/toast';
import type { ReconciliationResult, ReconciliationStatus } from '../lib/types';

const ALL_STATUSES: ReconciliationStatus[] = [
  'AMOUNT_MISMATCH',
  'MISSING_INTERNAL',
  'MISSING_EXTERNAL',
  'AMBIGUOUS_INTERNAL_MATCH',
  'MATCHED',
];

const STATUS_EXPLAINERS: Record<ReconciliationStatus, string> = {
  MATCHED: 'The external record and exactly one live ledger transaction agree on reference, amount and currency.',
  AMOUNT_MISMATCH:
    'Both sides exist and share a reference, but the amounts (or currencies) differ. The ledger is not changed - investigate which side is wrong.',
  MISSING_INTERNAL:
    'The external system reports a record that has no ledger transaction carrying that reference.',
  MISSING_EXTERNAL:
    'A live ledger transaction carries an external reference, but no imported external record has that id.',
  AMBIGUOUS_INTERNAL_MATCH:
    'More than one live ledger transaction shares this external reference. LEDGR will not pick one for you; a person has to decide which is real.',
};

// --- exception detail --------------------------------------------------------------

function ExceptionDetail({
  result,
  onClose,
}: {
  result: ReconciliationResult;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const toast = useToast();
  const { can } = useAuth();
  const investigate = useInvestigateReconciliationResult();
  const ext = result.external_transaction;

  return (
    <Modal
      open
      onClose={onClose}
      width="max-w-3xl"
      title={ext ? `External record ${ext.external_id}` : `Internal transaction ${result.internal_reference ?? ''}`}
      description="Exception details - read-only"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          {result.investigation_id ? (
            <Button variant="primary" onClick={() => navigate(`/app/investigations/${result.investigation_id}`)}>
              Open {result.investigation_case_number}
            </Button>
          ) : (
            result.status !== 'MATCHED' &&
            can('investigations:write') && (
              <Button
                variant="primary"
                disabled={investigate.isPending}
                icon={<SearchCheck className="h-3.5 w-3.5" />}
                onClick={() =>
                  investigate.mutate(result.id, {
                    onSuccess: (created) => {
                      toast.success(`Opened ${created.case_number}`);
                      navigate(`/app/investigations/${created.id}`);
                    },
                    onError: (error) => toast.apiError(error),
                  })
                }
              >
                Open investigation
              </Button>
            )
          )}
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <ReconStatusBadge status={result.status} />
          {result.difference && result.difference.amount_minor !== 0 && (
            <span className="text-[0.8125rem] text-ink-2">
              Difference: <Amount money={result.difference} showCurrency tone="auto" />
            </span>
          )}
        </div>

        <div>
          <p className="caption mb-1">Why</p>
          <p className="text-[0.875rem] leading-relaxed text-ink">{result.explanation}</p>
          <p className="mt-1 text-[0.8125rem] leading-relaxed text-ink-3">{STATUS_EXPLAINERS[result.status]}</p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-sm border border-border p-3">
            <p className="caption mb-2">Internal record (ledger)</p>
            {result.internal_transaction_id ? (
              <dl className="space-y-1.5 text-[0.8125rem]">
                <div>
                  <dt className="text-ink-3">Reference</dt>
                  <dd>
                    <Link
                      to={`/app/transactions/${result.internal_transaction_id}`}
                      className="num font-medium text-accent-ink hover:underline"
                    >
                      {result.internal_reference}
                    </Link>
                  </dd>
                </div>
                <div>
                  <dt className="text-ink-3">Amount</dt>
                  <dd>
                    <Amount money={result.internal_amount} showCurrency />
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="text-[0.8125rem] text-ink-3">
                {result.status === 'AMBIGUOUS_INTERNAL_MATCH'
                  ? 'Several candidates - none was chosen.'
                  : 'No ledger transaction matches.'}
              </p>
            )}
          </div>

          <div className="rounded-sm border border-border p-3">
            <p className="caption mb-2">External record</p>
            {ext ? (
              <dl className="space-y-1.5 text-[0.8125rem]">
                <div>
                  <dt className="text-ink-3">Source</dt>
                  <dd className="text-ink">{ext.source_system}</dd>
                </div>
                <div>
                  <dt className="text-ink-3">Reference</dt>
                  <dd className="num text-ink">{ext.external_id}</dd>
                </div>
                <div>
                  <dt className="text-ink-3">Amount</dt>
                  <dd>
                    <Amount money={ext.amount} showCurrency />
                  </dd>
                </div>
                <div>
                  <dt className="text-ink-3">Timestamp / status</dt>
                  <dd className="text-ink">
                    {formatDateTime(ext.transaction_timestamp)} · {ext.status}
                  </dd>
                </div>
                {ext.description && (
                  <div>
                    <dt className="text-ink-3">Description</dt>
                    <dd className="text-ink">{ext.description}</dd>
                  </div>
                )}
              </dl>
            ) : (
              <p className="text-[0.8125rem] text-ink-3">No external record exists for this transaction.</p>
            )}
          </div>
        </div>

        <ExceptionReview result={result} />
      </div>
    </Modal>
  );
}

// --- page ----------------------------------------------------------------------------------

export default function ReconciliationPage() {
  const { can } = useAuth();
  const toast = useToast();
  const canRun = can('reconciliation:run');

  const [selectedRunId, setSelectedRunId] = useState<string | undefined>();
  const [statusFilter, setStatusFilter] = useState<ReconciliationStatus | 'EXCEPTIONS' | 'ALL'>('EXCEPTIONS');
  const [search, setSearch] = useState('');
  const [detail, setDetail] = useState<ReconciliationResult | null>(null);
  const [importing, setImporting] = useState(false);
  // Large runs can hold thousands of rows; render them in pages of 100.
  const [shown, setShown] = useState(100);

  const runs = useReconciliationRuns({ limit: 25 });
  const sources = useReconciliationSources();
  const runId = selectedRunId ?? runs.data?.items[0]?.id;
  const run = useReconciliationRun(runId);

  const loadDemo = useLoadDemoData();
  const runReconciliation = useRunReconciliation();

  const summary = run.data;
  const exceptionCount = summary
    ? summary.mismatch_count + summary.missing_internal_count + summary.missing_external_count + summary.ambiguous_count
    : 0;

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    return (summary?.results ?? []).filter((r) => {
      if (statusFilter === 'EXCEPTIONS' && r.status === 'MATCHED') return false;
      if (statusFilter !== 'EXCEPTIONS' && statusFilter !== 'ALL' && r.status !== statusFilter) return false;
      if (!term) return true;
      return [r.external_transaction?.external_id, r.internal_reference, r.external_transaction?.source_system]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(term));
    });
  }, [summary, statusFilter, search]);

  const counts: Record<ReconciliationStatus, number> = {
    MATCHED: summary?.matched_count ?? 0,
    AMOUNT_MISMATCH: summary?.mismatch_count ?? 0,
    MISSING_INTERNAL: summary?.missing_internal_count ?? 0,
    MISSING_EXTERNAL: summary?.missing_external_count ?? 0,
    AMBIGUOUS_INTERNAL_MATCH: summary?.ambiguous_count ?? 0,
  };

  const hasData = (sources.data?.length ?? 0) > 0;

  return (
    <>
      <PageHeader
        title="Reconciliation"
        description="Compare external records with the ledger - deterministic, read-only, auditable."
        actions={
          canRun && (
            <div className="flex flex-wrap items-center gap-2">
              <Button icon={<UploadCloud className="h-3.5 w-3.5" />} onClick={() => setImporting(true)}>
                Import records
              </Button>
              <Button
                variant="primary"
                icon={
                  runReconciliation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <PlayCircle className="h-3.5 w-3.5" />
                  )
                }
                disabled={runReconciliation.isPending || !hasData}
                title={!hasData ? 'Import external records first' : undefined}
                onClick={() =>
                  runReconciliation.mutate(undefined, {
                    onSuccess: (created) => {
                      setSelectedRunId(created.id);
                      setStatusFilter('EXCEPTIONS');
                      toast.success(
                        `Run #${created.run_number} complete`,
                        `${created.matched_count} matched, ${
                          created.mismatch_count +
                          created.missing_internal_count +
                          created.missing_external_count +
                          created.ambiguous_count
                        } exception(s)`,
                      );
                    },
                    onError: (error) => toast.apiError(error),
                  })
                }
              >
                Run reconciliation
              </Button>
            </div>
          )
        }
      />

      <div className="space-y-5 px-5 py-6 sm:px-8">
        {/* sources */}
        <Panel>
          <PanelHeader title="Sources" description="External systems whose records have been imported" />
          {sources.isLoading ? (
            <div className="p-4">
              <Skeleton className="h-8 w-64" />
            </div>
          ) : sources.data && sources.data.length > 0 ? (
            <ul className="flex flex-wrap gap-2 px-4 py-3">
              {sources.data.map((s) => (
                <li key={s.source_system} className="rounded-sm border border-border bg-surface-2 px-3 py-2 text-[0.8125rem]">
                  <span className="num font-medium text-ink">{s.source_system}</span>
                  <span className="ml-2 text-ink-3">
                    {s.record_count} record{s.record_count === 1 ? '' : 's'}
                    {s.last_imported_at && ` · imported ${formatRelative(s.last_imported_at)}`}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <div className="px-4 py-3 text-[0.8125rem] text-ink-3">No external records imported yet.</div>
          )}
        </Panel>

        {runs.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : runs.isError ? (
          <Panel>
            <ErrorState error={runs.error} onRetry={() => runs.refetch()} context="reconciliation runs" />
          </Panel>
        ) : !runs.data || runs.data.items.length === 0 ? (
          <Panel>
            <EmptyState
              icon={<SearchCheck className="h-4 w-4" strokeWidth={1.6} />}
              title={hasData ? 'No reconciliation has been run yet' : 'Nothing to reconcile yet'}
              description={
                canRun
                  ? hasData
                    ? 'Run a reconciliation to compare the imported records with the ledger.'
                    : 'Import records from your bank or payment processor - or load a small demo scenario to see how it works.'
                  : 'No reconciliation has been run. Someone with reconciliation access needs to run one.'
              }
              action={
                canRun && (
                  <div className="flex flex-wrap justify-center gap-2">
                    <Button size="sm" variant="primary" onClick={() => setImporting(true)}>
                      Import records
                    </Button>
                    {can('org:manage') && (
                      <Button
                        size="sm"
                        disabled={loadDemo.isPending}
                        onClick={() =>
                          loadDemo.mutate(undefined, {
                            onSuccess: (r) => toast.success('Demo data loaded', r.message),
                            onError: (error) => toast.apiError(error),
                          })
                        }
                      >
                        Load demo data
                      </Button>
                    )}
                  </div>
                )
              }
            />
          </Panel>
        ) : (
          <>
            <Panel>
              <PanelHeader
                title={`Run #${summary?.run_number ?? runs.data.items[0].run_number}`}
                description={summary && `${formatDateTime(summary.created_at)} · ${summary.external_count} external records compared`}
                actions={
                  <Select
                    aria-label="Choose a run"
                    className="h-8 w-52"
                    value={runId ?? ''}
                    onChange={(e) => setSelectedRunId(e.target.value)}
                  >
                    {runs.data.items.map((r) => (
                      <option key={r.id} value={r.id}>
                        Run #{r.run_number} · {formatDateTime(r.created_at)}
                      </option>
                    ))}
                  </Select>
                }
              />
              {run.isLoading ? (
                <div className="p-4">
                  <Skeleton className="h-20 w-full" />
                </div>
              ) : (
                summary && (
                  <div className="grid grid-cols-2 gap-px bg-border sm:grid-cols-3 lg:grid-cols-5">
                    {ALL_STATUSES.map((status) => (
                      <button
                        key={status}
                        type="button"
                        onClick={() => {
                          setStatusFilter(status);
                          setShown(100);
                        }}
                        aria-pressed={statusFilter === status}
                        className={cn(
                          'bg-surface px-5 py-4 text-left transition-colors hover:bg-surface-2',
                          statusFilter === status && 'bg-accent-soft hover:bg-accent-soft',
                        )}
                      >
                        <p className="caption">{RECON_LABELS[status]}</p>
                        <p
                          className={cn(
                            'num mt-1 text-2xl font-semibold',
                            status === 'MATCHED'
                              ? 'text-positive'
                              : counts[status] > 0
                                ? status === 'AMOUNT_MISMATCH'
                                  ? 'text-negative'
                                  : 'text-warning'
                                : 'text-ink',
                          )}
                        >
                          {counts[status]}
                        </p>
                      </button>
                    ))}
                  </div>
                )
              )}
            </Panel>

            <Panel>
              <PanelHeader
                title="Results"
                description={`${exceptionCount} exception${exceptionCount === 1 ? '' : 's'} need review. Select a row for the full picture.`}
              />
              <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
                <div className="relative min-w-52 flex-1 sm:max-w-xs">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-3" />
                  <Input
                    aria-label="Search results"
                    className="pl-8"
                    placeholder="External id, reference or source"
                    value={search}
                    onChange={(e) => {
                      setSearch(e.target.value);
                      setShown(100);
                    }}
                  />
                </div>
                <Select
                  aria-label="Filter by status"
                  className="w-48"
                  value={statusFilter}
                  onChange={(e) => {
                    setStatusFilter(e.target.value as typeof statusFilter);
                    setShown(100);
                  }}
                >
                  <option value="EXCEPTIONS">Exceptions only</option>
                  <option value="ALL">Everything</option>
                  {ALL_STATUSES.map((s) => (
                    <option key={s} value={s}>
                      {RECON_LABELS[s]}
                    </option>
                  ))}
                </Select>
              </div>

              {run.isLoading ? (
                <Skeleton className="m-4 h-48" />
              ) : visible.length === 0 ? (
                <EmptyState
                  title={
                    statusFilter === 'EXCEPTIONS' && !search && exceptionCount === 0
                      ? 'No exceptions - everything matched'
                      : 'No results match this filter'
                  }
                  description={
                    statusFilter === 'EXCEPTIONS' && !search && exceptionCount === 0
                      ? 'Every external record agrees with exactly one ledger transaction.'
                      : undefined
                  }
                  action={
                    statusFilter !== 'ALL' || search ? (
                      <Button
                        size="sm"
                        onClick={() => {
                          setStatusFilter('ALL');
                          setSearch('');
                        }}
                      >
                        Show everything
                      </Button>
                    ) : undefined
                  }
                />
              ) : (
                <TableScroller>
                  <Table>
                    <THead>
                      <th className="px-3 py-2 text-xs font-medium text-ink-3">Reference</th>
                      <th className="px-3 py-2 text-xs font-medium text-ink-3">Source</th>
                      <th className="px-3 py-2 text-right text-xs font-medium text-ink-3">External</th>
                      <th className="px-3 py-2 text-right text-xs font-medium text-ink-3">Internal</th>
                      <th className="px-3 py-2 text-right text-xs font-medium text-ink-3">Difference</th>
                      <th className="px-3 py-2 text-xs font-medium text-ink-3">Status</th>
                      <th className="px-3 py-2 text-xs font-medium text-ink-3">Case</th>
                    </THead>
                    <tbody className="divide-y divide-border">
                      {visible.slice(0, shown).map((result) => (
                        <tr
                          key={result.id}
                          tabIndex={0}
                          role="button"
                          aria-label={`Open details for ${result.external_transaction?.external_id ?? result.internal_reference}`}
                          className="cursor-pointer align-top hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none"
                          onClick={() => setDetail(result)}
                          onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && setDetail(result)}
                        >
                          <td className="num px-3 py-2.5 text-[0.8125rem] font-medium text-ink">
                            {result.external_transaction?.external_id ?? (
                              <span className="text-ink-3">{result.internal_reference}</span>
                            )}
                          </td>
                          <td className="px-3 py-2.5 text-xs text-ink-3">
                            {result.external_transaction?.source_system ?? '—'}
                          </td>
                          <td className="px-3 py-2.5 text-right">
                            <Amount money={result.external_transaction?.amount} showCurrency />
                          </td>
                          <td className="px-3 py-2.5 text-right">
                            <Amount money={result.internal_amount} showCurrency />
                          </td>
                          <td className="px-3 py-2.5 text-right">
                            {result.difference && result.difference.amount_minor !== 0 ? (
                              <Amount money={result.difference} showCurrency tone="auto" />
                            ) : (
                              <span className="text-ink-3">—</span>
                            )}
                          </td>
                          <td className="px-3 py-2.5">
                            <ReconStatusBadge status={result.status} />
                          </td>
                          <td className="px-3 py-2.5">
                            {result.investigation_case_number ? (
                              <Badge tone="accent">{result.investigation_case_number}</Badge>
                            ) : (
                              <span className="text-ink-3">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </TableScroller>
              )}
              {visible.length > shown && (
                <div className="flex items-center justify-between border-t border-border px-4 py-2.5">
                  <p className="text-xs text-ink-3">
                    Showing <span className="num text-ink-2">{shown}</span> of{' '}
                    <span className="num text-ink-2">{visible.length}</span> results
                  </p>
                  <Button size="sm" onClick={() => setShown((n) => n + 100)}>
                    Show 100 more
                  </Button>
                </div>
              )}
            </Panel>
          </>
        )}
      </div>

      <ImportWizard open={importing} onClose={() => setImporting(false)} />
      {detail && <ExceptionDetail result={detail} onClose={() => setDetail(null)} />}
    </>
  );
}
