/**
 * External Reconciliation.
 *
 * Compares external evidence (a payment processor's own records) against
 * LEDGR's internal ledger, deterministically - same account, same amount,
 * same external reference. No AI, no similarity scores. Read-only with
 * respect to the ledger: this page can load demo evidence and run a
 * comparison, but the only way any exception here changes financial state
 * is through the existing correction workflow, reached via "Investigate."
 */

import { Loader2, PlayCircle, SearchCheck, UploadCloud } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { Amount } from '../components/ledger/atoms';
import { Badge, Button, Panel, PanelHeader, Skeleton } from '../components/ui/primitives';
import { EmptyState, ErrorState, Table, TableScroller, THead } from '../components/ui/Table';
import { formatDateTime } from '../lib/format';
import {
  useImportDemoReconciliationData,
  useInvestigateReconciliationResult,
  useReconciliationRun,
  useReconciliationRuns,
  useRunReconciliation,
} from '../lib/queries';
import type { ReconciliationResult, ReconciliationStatus } from '../lib/types';

const STATUS_LABEL: Record<ReconciliationStatus, string> = {
  MATCHED: 'Matched',
  AMOUNT_MISMATCH: 'Mismatch',
  MISSING_INTERNAL: 'Missing internal',
  MISSING_EXTERNAL: 'Missing external',
  AMBIGUOUS_INTERNAL_MATCH: 'Ambiguous match',
};

function StatusBadge({ status }: { status: ReconciliationStatus }) {
  const tone = status === 'MATCHED' ? 'positive' : status === 'AMOUNT_MISMATCH' ? 'warning' : 'negative';
  return <Badge tone={tone}>{STATUS_LABEL[status]}</Badge>;
}

function InvestigateButton({ result }: { result: ReconciliationResult }) {
  const navigate = useNavigate();
  const investigate = useInvestigateReconciliationResult();

  if (result.status === 'MATCHED') return null;

  return (
    <Button
      size="sm"
      variant="primary"
      disabled={investigate.isPending}
      icon={
        investigate.isPending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <SearchCheck className="h-3.5 w-3.5" />
        )
      }
      onClick={() =>
        investigate.mutate(result.id, {
          onSuccess: (created) => navigate(`/app/investigations/${created.id}`),
        })
      }
    >
      Investigate
    </Button>
  );
}

export default function ReconciliationPage() {
  const [selectedRunId, setSelectedRunId] = useState<string | undefined>();

  const runs = useReconciliationRuns({ limit: 10 });
  const latestRunId = selectedRunId ?? runs.data?.items[0]?.id;
  const run = useReconciliationRun(latestRunId);

  const importDemo = useImportDemoReconciliationData();
  const runReconciliation = useRunReconciliation();

  return (
    <>
      <PageHeader
        title="Reconciliation"
        description="Compare external evidence against the internal ledger - deterministic, read-only, auditable."
        actions={
          <div className="flex items-center gap-2">
            <Button
              icon={
                importDemo.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <UploadCloud className="h-3.5 w-3.5" />
                )
              }
              disabled={importDemo.isPending}
              onClick={() => importDemo.mutate()}
            >
              Load demo data
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
              disabled={runReconciliation.isPending}
              onClick={() =>
                runReconciliation.mutate(undefined, {
                  onSuccess: (created) => setSelectedRunId(created.id),
                })
              }
            >
              Run reconciliation
            </Button>
          </div>
        }
      />

      <div className="space-y-4 px-5 py-6 sm:px-8">
        {importDemo.isSuccess && (
          <div className="rounded-md border border-positive/30 bg-positive-soft px-4 py-2.5 text-[0.8125rem] text-positive">
            Loaded {importDemo.data.imported} external record{importDemo.data.imported === 1 ? '' : 's'}
            {importDemo.data.skipped_duplicates > 0 &&
              ` (${importDemo.data.skipped_duplicates} already loaded)`}
            . Click "Run reconciliation" to compare them against the ledger.
          </div>
        )}

        {runs.isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : runs.isError ? (
          <Panel>
            <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
          </Panel>
        ) : !runs.data || runs.data.items.length === 0 ? (
          <Panel>
            <EmptyState
              icon={<SearchCheck className="h-4 w-4" strokeWidth={1.6} />}
              title="No reconciliation runs yet"
              description="Load the demo external dataset, then run reconciliation to compare it against the ledger."
              action={
                <Button variant="primary" size="sm" onClick={() => importDemo.mutate()}>
                  Load demo data
                </Button>
              }
            />
          </Panel>
        ) : (
          <>
            <Panel>
              <PanelHeader
                title={`Run #${run.data?.run_number ?? runs.data.items[0].run_number}`}
                description={run.data && `Completed ${formatDateTime(run.data.created_at)}`}
              />
              {run.isLoading ? (
                <Skeleton className="h-24 w-full" />
              ) : (
                run.data && (
                  <div className="grid grid-cols-2 divide-x divide-y divide-border sm:grid-cols-3 lg:grid-cols-6 sm:divide-y-0">
                    <div className="px-5 py-4">
                      <p className="caption">External records</p>
                      <p className="num mt-1 text-2xl font-semibold text-ink">{run.data.external_count}</p>
                    </div>
                    <div className="px-5 py-4">
                      <p className="caption">Matched</p>
                      <p className="num mt-1 text-2xl font-semibold text-positive">
                        {run.data.matched_count}
                      </p>
                    </div>
                    <div className="px-5 py-4">
                      <p className="caption">Amount mismatches</p>
                      <p className="num mt-1 text-2xl font-semibold text-warning">
                        {run.data.mismatch_count}
                      </p>
                    </div>
                    <div className="px-5 py-4">
                      <p className="caption">Missing internal</p>
                      <p className="num mt-1 text-2xl font-semibold text-negative">
                        {run.data.missing_internal_count}
                      </p>
                    </div>
                    <div className="px-5 py-4">
                      <p className="caption">Missing external</p>
                      <p className="num mt-1 text-2xl font-semibold text-negative">
                        {run.data.missing_external_count}
                      </p>
                    </div>
                    <div className="px-5 py-4">
                      <p className="caption">Ambiguous</p>
                      <p className="num mt-1 text-2xl font-semibold text-negative">
                        {run.data.ambiguous_count}
                      </p>
                    </div>
                  </div>
                )
              )}
            </Panel>

            <Panel>
              <PanelHeader
                title="Reconciliation results"
                description="Every external record compared against the ledger by external reference - never a guess."
              />
              {run.isLoading ? (
                <Skeleton className="h-64 w-full" />
              ) : !run.data || run.data.results.length === 0 ? (
                <EmptyState title="No external records to reconcile" />
              ) : (
                <TableScroller>
                  <Table>
                    <THead>
                      <th className="th">External ID</th>
                      <th className="th">Source</th>
                      <th className="th text-right">External amount</th>
                      <th className="th text-right">Internal amount</th>
                      <th className="th">Status</th>
                      <th className="th text-right">Difference</th>
                      <th className="th" />
                    </THead>
                    <tbody>
                      {run.data.results.map((result) => (
                        <tr key={result.id}>
                          <td className="td num font-medium">
                            {result.external_transaction ? (
                              result.external_transaction.external_id
                            ) : (
                              <span className="text-ink-3">— no external record</span>
                            )}
                          </td>
                          <td className="td text-xs text-ink-3">
                            {result.external_transaction?.source_system ?? '—'}
                          </td>
                          <td className="td text-right">
                            <Amount money={result.external_transaction?.amount} showCurrency />
                          </td>
                          <td className="td text-right">
                            <Amount money={result.internal_amount} showCurrency />
                          </td>
                          <td className="td">
                            <StatusBadge status={result.status} />
                            <p className="mt-1 max-w-xs text-2xs text-ink-3">{result.explanation}</p>
                          </td>
                          <td className="td text-right">
                            <Amount money={result.difference} showCurrency tone="auto" />
                          </td>
                          <td className="td text-right">
                            <InvestigateButton result={result} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </TableScroller>
              )}
            </Panel>
          </>
        )}
      </div>
    </>
  );
}
