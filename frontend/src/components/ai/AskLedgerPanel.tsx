/**
 * Ask LEDGR: a question box grounded entirely in read-only ledger tools.
 *
 * This component only ever calls `POST /ai/ask`. It never fetches account or
 * transaction data itself and never renders anything the backend didn't say -
 * the "tools used" footnote is there specifically so an operator can see
 * that the answer came from real lookups, not the model's own memory.
 */

import { ArrowRight, Sparkles } from 'lucide-react';
import { useState } from 'react';

import { useAskLedger } from '../../lib/queries';
import { Button, Input, Panel, PanelHeader, Spinner } from '../ui/primitives';
import { AIUnavailableNotice } from './AIUnavailableNotice';

const SUGGESTED_PROMPTS = [
  'What changed today?',
  'What were the largest transactions this month?',
  'How many transactions were posted this week?',
  'Summarize today’s ledger activity.',
];

export function AskLedgerPanel({ available }: { available: boolean }) {
  const [question, setQuestion] = useState('');
  const ask = useAskLedger();

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || ask.isPending) return;
    setQuestion(trimmed);
    ask.mutate(trimmed);
  };

  return (
    <Panel>
      <PanelHeader
        title="Ask LEDGR"
        description="Answers are grounded in real ledger data, retrieved through fixed, read-only lookups."
      />
      <div className="space-y-3 px-4 py-4">
        {!available ? (
          <AIUnavailableNotice />
        ) : (
          <>
            <div className="flex items-center gap-2">
              <Input
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && submit(question)}
                placeholder="Ask about your ledger…"
                disabled={ask.isPending}
              />
              <Button
                variant="primary"
                onClick={() => submit(question)}
                disabled={ask.isPending || !question.trim()}
                icon={ask.isPending ? <Spinner /> : <ArrowRight className="h-3.5 w-3.5" />}
              >
                Ask
              </Button>
            </div>

            <div className="flex flex-wrap gap-1.5">
              {SUGGESTED_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => submit(prompt)}
                  disabled={ask.isPending}
                  className="rounded-full border border-border px-3 py-1 text-[0.75rem] text-ink-2 transition-colors hover:border-border-strong hover:text-ink disabled:opacity-50"
                >
                  {prompt}
                </button>
              ))}
            </div>

            {ask.isPending && (
              <p className="flex items-center gap-2 text-[0.8125rem] text-ink-3">
                <Spinner /> Checking the ledger…
              </p>
            )}

            {ask.isError && (
              <p className="text-[0.8125rem] text-negative">{ask.error.message}</p>
            )}

            {ask.data && (
              <div className="rounded-md border border-border bg-surface-2 px-4 py-3">
                <p className="flex items-start gap-2 text-[0.9375rem] leading-relaxed text-ink">
                  <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-accent-ink" strokeWidth={1.7} />
                  {ask.data.answer}
                </p>
                {ask.data.tools_used.length > 0 && (
                  <p className="mt-2 text-[0.75rem] text-ink-3">
                    Looked up: {ask.data.tools_used.join(', ')}
                  </p>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
