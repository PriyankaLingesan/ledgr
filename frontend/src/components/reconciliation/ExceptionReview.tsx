/**
 * Review tools for one reconciliation exception: deterministic match
 * suggestions, a recorded human decision, and an optional AI explanation.
 *
 * None of this changes the result. The engine's status (e.g. AMBIGUOUS) is the
 * deterministic verdict and stays as computed; a decision is an annotation with
 * an author and a reason, and the AI answer is a draft with cited sources.
 */

import { Lightbulb, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { useAuth } from '../../lib/auth';
import { formatDateTime } from '../../lib/format';
import { useExplainResult, useMatchSuggestions, useRecordDecision } from '../../lib/opsQueries';
import { useAIStatus } from '../../lib/queries';
import { useToast } from '../../lib/toast';
import type { ReconciliationResult } from '../../lib/types';
import { CopilotAnswerView, copilotErrorMessage } from '../ai/CopilotPanel';
import { Amount } from '../ledger/atoms';
import { Badge, Button, Spinner, Textarea } from '../ui/primitives';

export function ExceptionReview({ result }: { result: ReconciliationResult }) {
  const { can } = useAuth();
  const toast = useToast();
  const aiStatus = useAIStatus();
  const canDecide = can('reconciliation:run') && result.status !== 'MATCHED';
  const suggestions = useMatchSuggestions(result.id, result.status !== 'MATCHED' && result.external_transaction !== null);
  const decide = useRecordDecision();
  const explain = useExplainResult();

  const [choosing, setChoosing] = useState<{ kind: 'CONFIRMED_MATCH' | 'NOT_A_MATCH'; transactionId?: string } | null>(null);
  const [reason, setReason] = useState('');

  const submit = () => {
    if (!choosing || !reason.trim()) return;
    decide.mutate(
      {
        resultId: result.id,
        decision: choosing.kind,
        internal_transaction_id: choosing.transactionId ?? null,
        reason: reason.trim(),
      },
      {
        onSuccess: () => {
          toast.success('Decision recorded', 'The result itself is unchanged; your conclusion is now on the audit trail.');
          setChoosing(null);
          setReason('');
        },
        onError: (error) => toast.apiError(error),
      },
    );
  };

  if (result.status === 'MATCHED') return null;

  return (
    <div className="space-y-4 border-t border-border pt-4">
      {result.latest_decision && (
        <div className="rounded-sm border border-border bg-surface-2 px-3 py-2.5 text-[0.8125rem]">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={result.latest_decision.decision === 'CONFIRMED_MATCH' ? 'positive' : 'neutral'}>
              {result.latest_decision.decision === 'CONFIRMED_MATCH' ? 'Reviewer confirmed a match' : 'Reviewer: not a match'}
            </Badge>
            <span className="text-ink-3">
              {result.latest_decision.decided_by} · {formatDateTime(result.latest_decision.created_at)}
            </span>
          </div>
          <p className="mt-1.5 text-ink-2">“{result.latest_decision.reason}”</p>
          <p className="mt-1 text-xs text-ink-3">
            The engine&apos;s verdict ({result.status}) is unchanged - this is a recorded human conclusion, not a ledger change.
          </p>
        </div>
      )}

      {result.external_transaction && (
        <section>
          <h3 className="caption mb-2 flex items-center gap-1.5">
            <Lightbulb className="h-3.5 w-3.5" /> Possible matches (suggestions - review required)
          </h3>
          {suggestions.isLoading ? (
            <p className="flex items-center gap-2 text-[0.8125rem] text-ink-3">
              <Spinner /> Looking for candidates…
            </p>
          ) : suggestions.data && suggestions.data.length > 0 ? (
            <ul className="space-y-2">
              {suggestions.data.map((s) => (
                <li key={s.transaction_id} className="rounded-sm border border-border p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Link to={`/app/transactions/${s.transaction_id}`} className="num text-[0.8125rem] font-medium text-accent-ink hover:underline">
                      {s.reference}
                    </Link>
                    <Amount money={s.amount} showCurrency />
                  </div>
                  <p className="mt-0.5 truncate text-xs text-ink-3">{s.description}</p>
                  <ul className="mt-1.5 list-inside list-disc text-xs text-ink-2">
                    {s.reasons.map((r) => (
                      <li key={r}>{r}</li>
                    ))}
                  </ul>
                  {canDecide && (
                    <Button
                      size="sm"
                      className="mt-2"
                      onClick={() => setChoosing({ kind: 'CONFIRMED_MATCH', transactionId: s.transaction_id })}
                    >
                      Confirm as match…
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[0.8125rem] text-ink-3">No ledger transaction shares this record&apos;s amount or identifier.</p>
          )}
        </section>
      )}

      {canDecide && !choosing && (
        <Button size="sm" onClick={() => setChoosing({ kind: 'NOT_A_MATCH' })}>
          Record “not a match”…
        </Button>
      )}

      {choosing && (
        <div className="rounded-sm border border-accent/30 bg-accent-soft/40 p-3">
          <p className="text-[0.8125rem] font-medium text-ink">
            {choosing.kind === 'CONFIRMED_MATCH' ? 'Confirm this match' : 'Record that nothing matches'}
          </p>
          <label htmlFor="decision-reason" className="field-label mt-2 block">
            Reason (required, kept on the audit trail)
          </label>
          <Textarea id="decision-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="mt-2 flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setChoosing(null)} disabled={decide.isPending}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={submit} disabled={decide.isPending || !reason.trim()}>
              Record decision
            </Button>
          </div>
        </div>
      )}

      {can('ai:use') && aiStatus.data?.available && (
        <section>
          <Button
            size="sm"
            icon={explain.isPending ? <Spinner /> : <Sparkles className="h-3.5 w-3.5" />}
            disabled={explain.isPending}
            onClick={() => explain.mutate(result.id)}
          >
            Explain with AI
          </Button>
          {explain.isError && (
            <p role="alert" className="mt-2 text-[0.8125rem] text-negative">
              {copilotErrorMessage(explain.error)}
            </p>
          )}
          {explain.data && (
            <div className="mt-2 rounded-sm border border-border">
              <CopilotAnswerView answer={explain.data} />
            </div>
          )}
        </section>
      )}
    </div>
  );
}
