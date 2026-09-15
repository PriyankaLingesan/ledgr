/**
 * AI Transaction Assistant: describe a transaction in plain language, get an
 * editable, unposted draft back.
 *
 * This panel only ever calls `POST /ai/transactions/propose`, which itself
 * never writes anything - see `ProposalReviewCard` for what "Post" actually
 * does.
 */

import { CheckCircle2, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { useProposeTransaction } from '../../lib/queries';
import { Button, Panel, PanelHeader, Spinner, Textarea } from '../ui/primitives';
import { AIUnavailableNotice } from './AIUnavailableNotice';
import { ProposalReviewCard } from './ProposalReviewCard';

export function TransactionAssistantPanel({ available }: { available: boolean }) {
  const [description, setDescription] = useState('');
  const [submittedDescription, setSubmittedDescription] = useState('');
  const [posted, setPosted] = useState<{ id: string; reference: string } | null>(null);
  const propose = useProposeTransaction();
  const navigate = useNavigate();

  const submit = () => {
    const trimmed = description.trim();
    if (!trimmed || propose.isPending) return;
    setPosted(null);
    setSubmittedDescription(trimmed);
    propose.mutate(trimmed);
  };

  const reset = () => {
    propose.reset();
    setDescription('');
    setPosted(null);
  };

  return (
    <Panel>
      <PanelHeader
        title="Describe a transaction"
        description="LEDGR drafts a balanced debit/credit proposal for you to review - it is never posted automatically."
      />
      <div className="space-y-3 px-4 py-4">
        {!available ? (
          <AIUnavailableNotice />
        ) : posted ? (
          <div className="flex items-center justify-between gap-3 rounded-md border border-positive/30 bg-positive-soft px-4 py-3">
            <p className="flex items-center gap-2 text-[0.875rem] text-positive">
              <CheckCircle2 className="h-4 w-4" strokeWidth={2} />
              Posted as <span className="num font-medium">{posted.reference}</span>
            </p>
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => navigate(`/app/transactions/${posted.id}`)}>
                View
              </Button>
              <Button size="sm" onClick={reset}>
                New
              </Button>
            </div>
          </div>
        ) : propose.data ? (
          <ProposalReviewCard
            proposal={propose.data}
            sourceDescription={submittedDescription}
            onPosted={setPosted}
            onReject={reset}
          />
        ) : (
          <>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="e.g. Received ₹50,000 from ABC for consulting."
              disabled={propose.isPending}
            />
            <Button
              variant="primary"
              onClick={submit}
              disabled={propose.isPending || !description.trim()}
              icon={propose.isPending ? <Spinner /> : <Sparkles className="h-3.5 w-3.5" />}
            >
              Draft transaction
            </Button>
            {propose.isError && (
              <p className="text-[0.8125rem] text-negative">{propose.error.message}</p>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
