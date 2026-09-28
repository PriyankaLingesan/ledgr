/**
 * Control Exception Engine, unified: every real signal LEDGR's own checks
 * already support - potential duplicates and failed integrity checks -
 * in one list, each with a one-click path into an investigation.
 *
 * No invented anomaly scoring, no magnitude thresholds beyond what the
 * duplicate detector and integrity checklist already compute server-side.
 * Nothing here is ever labelled fraud - "requires investigation" is as far
 * as this goes; the operator decides what a match actually means.
 */

import { Loader2, SearchCheck, ShieldAlert } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { Badge, Button, Panel, PanelHeader, Skeleton } from '../ui/primitives';
import { EmptyState, ErrorState } from '../ui/Table';
import { formatRelative } from '../../lib/format';
import { useControlExceptions, useCreateInvestigation } from '../../lib/queries';
import type { ControlException } from '../../lib/types';

function exceptionTone(kind: ControlException['kind']) {
  return kind === 'POTENTIAL_DUPLICATE' ? 'warning' : 'negative';
}

function InvestigateButton({ exception }: { exception: ControlException }) {
  const navigate = useNavigate();
  const createCase = useCreateInvestigation();

  return (
    <Button
      size="sm"
      variant="primary"
      disabled={createCase.isPending}
      icon={
        createCase.isPending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <SearchCheck className="h-3.5 w-3.5" />
        )
      }
      onClick={() =>
        createCase.mutate(
          {
            title: exception.title,
            issue_summary: exception.detail,
            source: exception.kind === 'POTENTIAL_DUPLICATE' ? 'DUPLICATE' : 'INTEGRITY',
            source_reference: exception.transaction_ids[0] ?? null,
            account_id: exception.account_id,
          },
          { onSuccess: (created) => navigate(`/app/investigations/${created.id}`) },
        )
      }
    >
      Investigate
    </Button>
  );
}

export function ControlExceptionsPanel({ limit }: { limit?: number }) {
  const exceptions = useControlExceptions();
  const items = limit ? exceptions.data?.slice(0, limit) : exceptions.data;

  return (
    <Panel>
      <PanelHeader
        title="Control exceptions"
        description="Real signals that warrant a look - never a verdict."
        actions={
          exceptions.data && exceptions.data.length > 0 ? (
            <Badge tone="warning">{exceptions.data.length} open</Badge>
          ) : (
            exceptions.data && <Badge tone="positive">None open</Badge>
          )
        }
      />
      {exceptions.isLoading ? (
        <div className="space-y-2 p-4">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : exceptions.isError ? (
        <ErrorState
          error={exceptions.error}
          context="control exceptions"
          onRetry={() => exceptions.refetch()}
        />
      ) : !items || items.length === 0 ? (
        <EmptyState
          icon={<ShieldAlert className="h-4 w-4" strokeWidth={1.6} />}
          title="No control exceptions"
          description="No potential duplicates and every integrity check is currently passing."
        />
      ) : (
        <ul className="divide-y divide-border">
          {items.map((exception) => (
            <li key={exception.id} className="flex items-start justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={exceptionTone(exception.kind)}>
                    {exception.kind === 'POTENTIAL_DUPLICATE' ? 'Potential duplicate' : 'Integrity'}
                  </Badge>
                  <span className="text-[0.875rem] font-medium text-ink">{exception.title}</span>
                </div>
                <p className="mt-1 text-[0.8125rem] text-ink-3">{exception.detail}</p>
                <p className="mt-1 text-2xs text-ink-3">Detected {formatRelative(exception.detected_at)}</p>
              </div>
              <InvestigateButton exception={exception} />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
