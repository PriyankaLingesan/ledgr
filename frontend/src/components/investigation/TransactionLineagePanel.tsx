/**
 * Transaction Lineage: where a financial event came from, what it touched,
 * and what happened to it afterward.
 *
 * Every node is a real transaction and every edge a real relationship
 * already on the schema - the `reverses_transaction_id` foreign key, or the
 * `metadata.corrects` paper trail `correct_transaction` writes. Nothing here
 * is inferred: a transaction with no reversal or correction renders as a
 * single node with no arrows, not a fabricated chain.
 */

import { ArrowRight, GitBranch } from 'lucide-react';
import { Link } from 'react-router-dom';

import { Amount } from '../ledger/atoms';
import { Badge, Panel, PanelHeader, Skeleton } from '../ui/primitives';
import { ErrorState } from '../ui/Table';
import { cn } from '../../lib/cn';
import { useTransactionLineage } from '../../lib/queries';
import type { TransactionLineage, TransactionNode } from '../../lib/types';

function LineageNode({
  node,
  role,
  current,
}: {
  node: TransactionNode;
  role: string;
  current?: boolean;
}) {
  return (
    <Link
      to={`/app/transactions/${node.id}`}
      className={cn(
        'row-link flex min-w-[13rem] flex-col gap-1.5 rounded-md border px-3.5 py-3',
        current ? 'border-accent/40 bg-accent-soft' : 'border-border bg-surface',
      )}
    >
      <div className="flex items-center gap-1.5">
        <Badge tone={node.status === 'REVERSED' ? 'warning' : 'positive'}>{node.status}</Badge>
        {node.kind === 'REVERSAL' && <Badge tone="accent">REVERSAL</Badge>}
      </div>
      <p className="caption">{role}</p>
      <p className="num text-[0.8125rem] font-medium text-ink">{node.reference}</p>
      <p className="truncate text-xs text-ink-3">{node.description}</p>
      <Amount money={node.total_debits} showCurrency className="mt-0.5 text-[0.8125rem]" />
    </Link>
  );
}

function buildChain(lineage: TransactionLineage): { node: TransactionNode; role: string }[] {
  const chain: { node: TransactionNode; role: string }[] = [];

  if (lineage.reverses) {
    chain.push({ node: lineage.reverses, role: 'Original transaction' });
    chain.push({ node: lineage.root, role: 'Reversal (this transaction)' });
  } else if (lineage.corrects) {
    chain.push({ node: lineage.corrects, role: 'Original transaction' });
    chain.push({ node: lineage.root, role: 'Corrected replacement (this transaction)' });
  } else {
    chain.push({ node: lineage.root, role: 'This transaction' });
  }

  if (lineage.reversed_by) {
    chain.push({ node: lineage.reversed_by, role: 'Reversal' });
  }
  if (lineage.corrected_by) {
    chain.push({ node: lineage.corrected_by, role: 'Corrected replacement' });
  }

  return chain;
}

export function TransactionLineagePanel({ transactionId }: { transactionId: string }) {
  const lineage = useTransactionLineage(transactionId);

  const hasRelationships =
    lineage.data &&
    (lineage.data.reverses ||
      lineage.data.reversed_by ||
      lineage.data.corrects ||
      lineage.data.corrected_by);

  return (
    <Panel>
      <PanelHeader
        title="Transaction lineage"
        description="Where this event came from, and what happened to it afterward - real relationships only."
      />
      {lineage.isLoading ? (
        <div className="flex gap-3 p-4">
          <Skeleton className="h-24 w-52" />
          <Skeleton className="h-24 w-52" />
        </div>
      ) : lineage.isError ? (
        <ErrorState error={lineage.error} context="the lineage" onRetry={() => lineage.refetch()} />
      ) : !lineage.data ? null : !hasRelationships ? (
        <div className="flex items-center gap-2.5 px-4 py-6 text-[0.8125rem] text-ink-3">
          <GitBranch className="h-4 w-4 shrink-0" strokeWidth={1.7} />
          Standalone transaction - never reversed, never corrected, and not itself a correction.
        </div>
      ) : (
        <div className="flex flex-wrap items-stretch gap-2 overflow-x-auto p-4">
          {buildChain(lineage.data).map((entry, index, array) => (
            <div key={entry.node.id} className="flex items-center gap-2">
              <LineageNode node={entry.node} role={entry.role} current={entry.node.id === transactionId} />
              {index < array.length - 1 && (
                <ArrowRight className="h-4 w-4 shrink-0 text-ink-3" strokeWidth={1.8} />
              )}
            </div>
          ))}
        </div>
      )}
      {lineage.data && lineage.data.accounts.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-border px-4 py-2.5">
          <span className="caption">Accounts affected</span>
          {lineage.data.accounts.map((account) => (
            <Link
              key={account.id}
              to={`/app/accounts/${account.id}`}
              className="num rounded-xs border border-border px-1.5 py-px text-2xs text-ink-2 hover:border-border-strong hover:text-ink"
            >
              {account.code}
            </Link>
          ))}
        </div>
      )}
    </Panel>
  );
}
