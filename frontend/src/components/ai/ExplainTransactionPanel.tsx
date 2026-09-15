/**
 * "Explain with AI" on the transaction detail page.
 *
 * Calls `GET /ai/transactions/{id}/explain`, which builds its prompt only
 * from that transaction's own stored fields - this component never sends
 * the model anything beyond the id, and never shows anything the backend
 * didn't return.
 */

import { Sparkles } from 'lucide-react';

import { useExplainTransaction } from '../../lib/queries';
import { Button, Panel, PanelHeader, Spinner } from '../ui/primitives';
import { AIUnavailableNotice } from './AIUnavailableNotice';

export function ExplainTransactionPanel({ transactionId }: { transactionId: string }) {
  const explain = useExplainTransaction();

  return (
    <Panel>
      <PanelHeader
        title="Explain with AI"
        description="A plain-language reading of this transaction's own recorded data."
        actions={
          <Button
            size="sm"
            onClick={() => explain.mutate(transactionId)}
            disabled={explain.isPending}
            icon={explain.isPending ? <Spinner /> : <Sparkles className="h-3.5 w-3.5" />}
          >
            {explain.data ? 'Re-explain' : 'Explain'}
          </Button>
        }
      />
      {(explain.data || explain.isError) && (
        <div className="px-4 py-3.5">
          {explain.isError ? (
            explain.error.message.toLowerCase().includes('unavailable') ? (
              <AIUnavailableNotice reason={explain.error.message} />
            ) : (
              <p className="text-[0.8125rem] text-negative">{explain.error.message}</p>
            )
          ) : explain.data ? (
            <p className="flex items-start gap-2 text-[0.9375rem] leading-relaxed text-ink">
              <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-accent-ink" strokeWidth={1.7} />
              {explain.data.explanation}
            </p>
          ) : null}
        </div>
      )}
    </Panel>
  );
}
