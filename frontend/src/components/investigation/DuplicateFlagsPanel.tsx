/**
 * Deterministic duplicate-posting candidates - no AI, no fraud verdicts.
 *
 * Every group here comes from a structural match (same account, same signed
 * amount, same direction, posted close together) computed server-side by a
 * plain SQL self-join. This is a prompt to look closer, not an accusation:
 * the copy is deliberately "requires investigation", and every entry links
 * to its real transaction so the operator can judge for themselves.
 */

import { ChevronRight, Search } from 'lucide-react';
import { Link } from 'react-router-dom';

import { Amount, DirectionBadge } from '../ledger/atoms';
import { Badge, Panel, PanelHeader, Skeleton } from '../ui/primitives';
import { formatDateTime } from '../../lib/format';
import { useDuplicates } from '../../lib/queries';

export function DuplicateFlagsPanel() {
  const duplicates = useDuplicates();

  return (
    <Panel>
      <PanelHeader
        title="Possible duplicate postings"
        description="Same account, amount and direction, posted close together - deterministic, not AI-judged."
      />
      {duplicates.isLoading ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </div>
      ) : !duplicates.data || duplicates.data.length === 0 ? (
        <p className="flex items-center gap-2 px-4 py-4 text-[0.8125rem] text-ink-3">
          <Search className="h-3.5 w-3.5" strokeWidth={1.8} />
          No matching pairs found.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {duplicates.data.map((group, index) => (
            <li key={index} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="warning">Possible duplicate</Badge>
                <span className="text-[0.8125rem] text-ink-3">{group.reasons.join(' · ')}</span>
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
                      <span className="ml-2 text-[0.8125rem] text-ink-3">{entry.account_code}</span>
                      <p className="truncate text-xs text-ink-3">{entry.description}</p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <DirectionBadge direction={entry.direction} />
                      <Amount money={entry.amount} showCurrency />
                      <span className="num text-2xs text-ink-3">
                        {formatDateTime(entry.created_at)}
                      </span>
                      <ChevronRight className="h-3.5 w-3.5 text-ink-3" />
                    </div>
                  </Link>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
