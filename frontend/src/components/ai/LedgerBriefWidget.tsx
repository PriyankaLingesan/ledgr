/**
 * AI Ledger Brief: real, computed ledger statistics, phrased in one sentence.
 *
 * The numbers in `stats` never come from the model - they come from
 * `GET /ai/ledger-brief`'s own aggregate query, the same one "Ask LEDGR"
 * uses. Refreshing re-runs that query and, only then, asks the model to
 * re-phrase it; the widget otherwise sits on a long cache so opening the
 * dashboard repeatedly doesn't repeatedly call the LLM.
 */

import { RefreshCw, Sparkles } from 'lucide-react';

import { formatRelative } from '../../lib/format';
import { useLedgerBrief } from '../../lib/queries';
import { Badge, IconButton, Panel, PanelHeader, Skeleton } from '../ui/primitives';
import { AIUnavailableNotice } from './AIUnavailableNotice';

export function LedgerBriefWidget() {
  const brief = useLedgerBrief(1);

  return (
    <Panel>
      <PanelHeader
        title="AI Ledger Brief"
        description="A summary of today's real ledger activity."
        actions={
          <IconButton
            title="Refresh"
            onClick={() => brief.refetch()}
            disabled={brief.isFetching}
          >
            <RefreshCw className={brief.isFetching ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} />
          </IconButton>
        }
      />
      <div className="px-4 py-4">
        {brief.isLoading ? (
          <Skeleton className="h-5 w-full" />
        ) : brief.isError ? (
          <p className="text-[0.8125rem] text-ink-3">
            The brief couldn't be generated right now.
          </p>
        ) : brief.data ? (
          <>
            <p className="flex items-start gap-2 text-[0.9375rem] leading-relaxed text-ink">
              <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-accent-ink" strokeWidth={1.7} />
              {brief.data.summary}
            </p>
            <div className="mt-3 flex items-center gap-2 text-[0.75rem] text-ink-3">
              <Badge tone={brief.data.is_ai_generated ? 'accent' : 'neutral'}>
                {brief.data.is_ai_generated ? 'AI-generated summary' : 'Computed summary'}
              </Badge>
              <span>Updated {formatRelative(brief.data.generated_at)}</span>
            </div>
            {!brief.data.is_ai_generated && (
              <div className="mt-2">
                <AIUnavailableNotice />
              </div>
            )}
          </>
        ) : null}
      </div>
    </Panel>
  );
}
