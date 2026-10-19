/**
 * AI copilot output: a *draft for review*, never an action.
 *
 * Verified facts each cite a source that really was in the case data (and link to
 * it); anything the model could not ground is shown separately, labelled as an
 * unverified hypothesis. Nothing on this surface can post, approve, match or
 * resolve - the only controls are "run" and "copy".
 */

import { Check, Copy, ExternalLink, ShieldQuestion, Sparkles, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { useAuth } from '../../lib/auth';
import { ApiError } from '../../lib/api';
import { useCaseCopilot } from '../../lib/opsQueries';
import type { CopilotAnswer, CopilotTaskKind } from '../../lib/opsTypes';
import { useAIStatus } from '../../lib/queries';
import { Badge, Button, Panel, PanelHeader, Spinner } from '../ui/primitives';

export function CopilotAnswerView({ answer }: { answer: CopilotAnswer }) {
  const [copied, setCopied] = useState(false);

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable: the text is selectable */
    }
  };

  const sourceFor = (id: string) => answer.sources.find((s) => s.id === id);

  return (
    <div className="space-y-4 px-4 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="accent">AI draft</Badge>
        <Badge tone={answer.evidence_sufficient ? 'positive' : 'warning'}>
          {answer.evidence_sufficient ? 'Evidence sufficient' : 'Evidence incomplete'}
        </Badge>
        <span className="text-xs text-ink-3">Requires your review</span>
      </div>

      <p className="text-[0.9375rem] leading-relaxed text-ink">{answer.summary}</p>

      {answer.ungrounded_claims_moved > 0 && (
        <p
          role="status"
          className="flex items-start gap-2 rounded-sm border border-warning/30 bg-warning-soft px-3 py-2 text-[0.8125rem] text-warning"
        >
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.9} />
          {answer.ungrounded_claims_moved} claim{answer.ungrounded_claims_moved === 1 ? '' : 's'} cited a
          source that is not in this case&apos;s data and {answer.ungrounded_claims_moved === 1 ? 'was' : 'were'}{' '}
          moved to hypotheses.
        </p>
      )}

      {answer.verified_facts.length > 0 && (
        <section>
          <h3 className="caption mb-1.5">Verified against records</h3>
          <ul className="space-y-1.5">
            {answer.verified_facts.map((fact, index) => {
              const source = sourceFor(fact.source);
              return (
                <li key={index} className="flex items-start gap-2 text-[0.875rem] text-ink">
                  <Check className="mt-1 h-3.5 w-3.5 shrink-0 text-positive" strokeWidth={2.2} />
                  <span>
                    {fact.statement}{' '}
                    {source?.link ? (
                      <Link
                        to={`/app${source.link}`}
                        className="inline-flex items-center gap-0.5 text-xs text-accent-ink hover:underline"
                      >
                        {source.label} <ExternalLink className="h-3 w-3" />
                      </Link>
                    ) : (
                      <span className="text-xs text-ink-3">[{source?.label ?? fact.source}]</span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {answer.hypotheses.length > 0 && (
        <section>
          <h3 className="caption mb-1.5">Hypotheses - not verified</h3>
          <ul className="space-y-1.5">
            {answer.hypotheses.map((h, index) => (
              <li key={index} className="flex items-start gap-2 text-[0.875rem] text-ink-2">
                <ShieldQuestion className="mt-1 h-3.5 w-3.5 shrink-0 text-warning" strokeWidth={1.9} />
                <span>
                  {h.statement}
                  {h.basis && <span className="block text-xs text-ink-3">Basis: {h.basis}</span>}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {answer.missing_evidence.length > 0 && (
        <section>
          <h3 className="caption mb-1.5">Still missing</h3>
          <ul className="list-inside list-disc space-y-1 text-[0.875rem] text-ink-2">
            {answer.missing_evidence.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </section>
      )}

      {answer.suggested_next_steps.length > 0 && (
        <section>
          <h3 className="caption mb-1.5">Suggested next steps (for a person to carry out)</h3>
          <ol className="list-inside list-decimal space-y-1 text-[0.875rem] text-ink-2">
            {answer.suggested_next_steps.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ol>
        </section>
      )}

      {answer.document && (
        <section>
          <div className="mb-1.5 flex items-center justify-between">
            <h3 className="caption">Draft text</h3>
            <Button
              size="sm"
              variant="ghost"
              icon={copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              onClick={() => copy(answer.document ?? '')}
            >
              {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-sm border border-border bg-surface-2 p-3 text-[0.8125rem] leading-relaxed text-ink">
            {answer.document}
          </pre>
        </section>
      )}

      <p className="border-t border-border pt-3 text-2xs leading-relaxed text-ink-3">{answer.disclaimer}</p>
    </div>
  );
}

export function copilotErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'ai_unavailable') return 'The AI assistant is unavailable right now. The case itself is unaffected.';
    if (error.code === 'ai_response_invalid') return 'The assistant returned an answer that could not be validated, so it was discarded. Try again.';
    if (error.code === 'rate_limited') return 'You are asking too quickly. Wait a moment and try again.';
    return error.message;
  }
  return 'Something went wrong.';
}

const TASKS: { task: CopilotTaskKind; label: string }[] = [
  { task: 'SUMMARIZE', label: 'Summarise' },
  { task: 'MISSING_EVIDENCE', label: "What's missing?" },
  { task: 'NEXT_STEPS', label: 'What next?' },
  { task: 'DRAFT_RESOLUTION', label: 'Draft resolution' },
  { task: 'DRAFT_REPORT', label: 'Draft report' },
];

/** The investigation copilot: choose a question, read a grounded draft. */
export function CaseCopilot({ caseId }: { caseId: string }) {
  const { can } = useAuth();
  const status = useAIStatus();
  const copilot = useCaseCopilot();
  const [active, setActive] = useState<CopilotTaskKind | null>(null);

  if (!can('ai:use')) return null;

  return (
    <Panel>
      <PanelHeader
        title={
          <span className="inline-flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-accent-ink" strokeWidth={1.8} /> AI copilot
          </span>
        }
        description="Grounded in this case's own records. Drafts only - it cannot change anything."
      />
      {status.data && !status.data.available ? (
        <p className="px-4 py-4 text-[0.875rem] text-ink-2">
          The AI assistant is not configured on this deployment. Everything else on this page works without it.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2 border-b border-border px-4 py-3">
            {TASKS.map(({ task, label }) => (
              <Button
                key={task}
                size="sm"
                disabled={copilot.isPending}
                variant={active === task && copilot.data ? 'primary' : 'secondary'}
                onClick={() => {
                  setActive(task);
                  copilot.mutate({ caseId, task });
                }}
              >
                {label}
              </Button>
            ))}
          </div>
          {copilot.isPending && (
            <div className="flex items-center gap-2 px-4 py-5 text-[0.875rem] text-ink-3" role="status">
              <Spinner /> Reading the case and drafting…
            </div>
          )}
          {copilot.isError && (
            <p role="alert" className="px-4 py-4 text-[0.875rem] text-negative">
              {copilotErrorMessage(copilot.error)}
            </p>
          )}
          {copilot.data && !copilot.isPending && <CopilotAnswerView answer={copilot.data} />}
          {!copilot.data && !copilot.isPending && !copilot.isError && (
            <p className="px-4 py-4 text-[0.8125rem] text-ink-3">Choose a question above.</p>
          )}
        </>
      )}
    </Panel>
  );
}
