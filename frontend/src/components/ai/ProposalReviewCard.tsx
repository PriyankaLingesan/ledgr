/**
 * Review card for an AI-generated transaction proposal.
 *
 * "Post" here calls the exact same `useCreateTransaction()` mutation the
 * hand-written form uses, against the exact same `POST /transactions`
 * endpoint, with the proposal's own `post_body` sent unmodified - so every
 * existing guarantee (balance validation, atomicity, idempotency,
 * concurrency control) applies to an AI-originated posting exactly as it
 * does to a hand-typed one. This component never talks to `/ai/*` again
 * once a proposal is in hand.
 */

import { AlertTriangle, Check, Sparkles, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { Amount, DirectionBadge } from '../ledger/atoms';
import { Badge, Button, Spinner } from '../ui/primitives';
import { useCreateTransaction } from '../../lib/queries';
import type { TransactionProposal } from '../../lib/types';

export function ProposalReviewCard({
  proposal,
  sourceDescription,
  onPosted,
  onReject,
}: {
  proposal: TransactionProposal;
  /** The operator's original free-text request, shown for context. */
  sourceDescription: string;
  onPosted: (posted: { id: string; reference: string }) => void;
  onReject: () => void;
}) {
  const navigate = useNavigate();
  const createTransaction = useCreateTransaction();

  const post = () => {
    if (!proposal.post_body) return;
    createTransaction.mutate(proposal.post_body, {
      onSuccess: (transaction) => onPosted({ id: transaction.id, reference: transaction.reference }),
    });
  };

  const edit = () => {
    if (!proposal.post_body) return;
    navigate('/app/transactions/new', { state: { aiProposal: proposal.post_body } });
  };

  return (
    <div className="rounded-md border border-accent/30 bg-surface">
      <div className="flex items-center justify-between gap-3 border-b border-accent/20 bg-accent-soft px-4 py-2.5">
        <span className="flex items-center gap-2 text-[0.8125rem] font-medium text-accent-ink">
          <Sparkles className="h-4 w-4" strokeWidth={1.8} />
          AI suggestion - not yet posted
        </span>
        {proposal.valid ? (
          <Badge tone="accent">Balanced</Badge>
        ) : (
          <Badge tone="warning">Needs correction</Badge>
        )}
      </div>

      <div className="px-4 py-3.5">
        <p className="text-[0.8125rem] text-ink-3">“{sourceDescription}”</p>
        <p className="mt-1.5 text-[0.9375rem] font-medium text-ink">{proposal.description}</p>

        {proposal.entries.length > 0 && (
          <ul className="mt-3 divide-y divide-border rounded-sm border border-border">
            {proposal.entries.map((entry, index) => (
              <li
                key={`${entry.account_code}-${index}`}
                className="flex items-center justify-between gap-3 px-3 py-2"
              >
                <span className="flex items-center gap-2 min-w-0">
                  <DirectionBadge direction={entry.direction} />
                  <span className="min-w-0 truncate">
                    <span className="num text-[0.8125rem] font-medium text-ink">
                      {entry.account_code}
                    </span>
                    {entry.account_name && (
                      <span className="ml-1.5 text-[0.8125rem] text-ink-3">
                        {entry.account_name}
                      </span>
                    )}
                  </span>
                </span>
                <Amount
                  money={entry.amount}
                  tone={entry.direction === 'DEBIT' ? 'debit' : 'credit'}
                  className="shrink-0"
                />
              </li>
            ))}
          </ul>
        )}

        {proposal.issues.length > 0 && (
          <div className="mt-3 rounded-sm border border-warning/30 bg-warning-soft px-3 py-2.5">
            <p className="flex items-center gap-1.5 text-[0.8125rem] font-medium text-warning">
              <AlertTriangle className="h-3.5 w-3.5" strokeWidth={2} />
              This transaction needs correction before it can be posted
            </p>
            <ul className="mt-1.5 space-y-0.5 text-[0.8125rem] text-ink-2">
              {proposal.issues.map((issue) => (
                <li key={issue}>- {issue}</li>
              ))}
            </ul>
          </div>
        )}

        {createTransaction.isError && (
          <p className="mt-3 text-[0.8125rem] text-negative">
            Posting failed: {createTransaction.error.message}
          </p>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
        <Button variant="ghost" size="sm" icon={<X className="h-3.5 w-3.5" />} onClick={onReject}>
          Reject
        </Button>
        <Button size="sm" onClick={edit} disabled={!proposal.post_body}>
          Edit
        </Button>
        <Button
          variant="primary"
          size="sm"
          disabled={!proposal.valid || createTransaction.isPending}
          icon={createTransaction.isPending ? <Spinner /> : <Check className="h-3.5 w-3.5" />}
          onClick={post}
        >
          Post transaction
        </Button>
      </div>
    </div>
  );
}
