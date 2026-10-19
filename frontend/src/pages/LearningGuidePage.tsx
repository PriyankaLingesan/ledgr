/**
 * Demo Learning Guide: twelve guided exercises on the fictional Meridian Commerce organization.
 *
 * This page only teaches and asks. Whether an exercise is done is decided by the server from the
 * real state of the books (`POST /demo/guide/{id}/verify`): opening a page never counts, and no
 * expected answer is ever sent to the browser.
 */

import { CheckCircle2, ChevronDown, ChevronRight, Download, FlaskConical, GraduationCap } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { AIUnavailableNotice } from '../components/ai/AIUnavailableNotice';
import { IdempotencyLab } from '../components/demo/IdempotencyLab';
import { PageHeader } from '../components/layout/AppShell';
import { Badge, Button, Input, Panel, PanelHeader, Skeleton } from '../components/ui/primitives';
import { EmptyState, ErrorState } from '../components/ui/Table';
import { ApiError, downloadFile } from '../lib/api';
import { useAuth } from '../lib/auth';
import { cn } from '../lib/cn';
import { useGuide, useIsDemo, useVerifyExercise, type Guide, type GuideExercise } from '../lib/demo';
import { formatDate, formatMinor } from '../lib/format';
import { EXERCISES, type ExerciseContent } from '../lib/learningContent';
import { useAIStatus } from '../lib/queries';
import { useToast } from '../lib/toast';

function Section({ heading, children }: { heading: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-2xs font-semibold uppercase tracking-[0.06em] text-ink-3">{heading}</h4>
      <div className="mt-1 text-[0.875rem] leading-relaxed text-ink-2">{children}</div>
    </div>
  );
}

function Bullets({ items, ordered }: { items: string[]; ordered?: boolean }) {
  const List = ordered ? 'ol' : 'ul';
  return (
    <List className={cn('space-y-1 pl-5', ordered ? 'list-decimal' : 'list-disc')}>
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </List>
  );
}

function SampleStatement({ guide }: { guide: Guide }) {
  const toast = useToast();
  return (
    <div className="rounded-md border border-border bg-surface-2 p-4">
      <p className="text-[0.875rem] font-semibold text-ink">Synthetic bank statement</p>
      <p className="mt-1 text-[0.8125rem] text-ink-2">
        {guide.statement.record_count} records for source system <code className="num">{guide.statement.source_system}</code>.
        Columns: external_id, amount, currency, direction, timestamp, description - the import wizard maps them
        automatically.
      </p>
      <Button
        className="mt-3"
        size="sm"
        icon={<Download className="h-3.5 w-3.5" />}
        onClick={() =>
          downloadFile('/demo/guide/sample-statement.csv', guide.statement.filename).catch((error) =>
            toast.apiError(error),
          )
        }
      >
        Download {guide.statement.filename}
      </Button>
    </div>
  );
}

function AiTool() {
  const status = useAIStatus();
  if (status.isLoading) return <Skeleton className="h-10 w-full" />;
  if (status.data?.available) {
    return (
      <p className="rounded-md border border-border bg-surface-2 px-4 py-3 text-[0.8125rem] text-ink-2">
        Live AI is configured on this installation ({status.data.provider}). Open any transaction and press
        Explain with AI.
      </p>
    );
  }
  return (
    <div className="space-y-2">
      <AIUnavailableNotice reason={status.data?.reason} />
      <p className="text-[0.8125rem] text-ink-2">
        This exercise needs a live model, so it cannot be completed here - every other exercise works without one.
        To enable it, set <code className="num">AI_PROVIDER</code>, <code className="num">AI_MODEL</code> and{' '}
        <code className="num">AI_API_KEY</code> for the API (see docs/AI.md). Nothing is faked in the meantime.
      </p>
    </div>
  );
}

function ExerciseCard({
  index,
  content,
  state,
  guide,
}: {
  index: number;
  content: ExerciseContent;
  state: GuideExercise;
  guide: Guide;
}) {
  const [open, setOpen] = useState(!state.completed && index === 0);
  const [answer, setAnswer] = useState('');
  const verify = useVerifyExercise();
  const toast = useToast();
  const { can } = useAuth();
  const canVerify = can('investigations:write');
  const result = verify.data && verify.data.exercise_id === content.id ? verify.data : null;
  const facts = state.facts;

  const check = () =>
    verify.mutate(
      { id: content.id, answer: content.answer ? answer : undefined },
      { onError: (error) => toast.apiError(error) },
    );

  return (
    <li>
      <Panel>
        <button
          type="button"
          className="flex w-full items-center gap-3 px-4 py-3.5 text-left"
          aria-expanded={open}
          aria-controls={`exercise-${content.id}`}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <ChevronDown className="h-4 w-4 text-ink-3" /> : <ChevronRight className="h-4 w-4 text-ink-3" />}
          <span className="num w-6 text-ink-3">{index + 1}</span>
          <span className="min-w-0 flex-1 truncate text-[0.9375rem] font-semibold text-ink">{content.title}</span>
          {state.completed ? (
            <Badge tone="positive">
              <CheckCircle2 className="h-3 w-3" aria-hidden /> Completed
            </Badge>
          ) : (
            <Badge tone="neutral">{state.kind === 'answer' ? 'Find & answer' : 'Do it'}</Badge>
          )}
        </button>

        {open && (
          <div id={`exercise-${content.id}`} className="space-y-4 border-t border-border px-4 py-4">
            <Section heading="Business context">
              {content.context(facts)}
            </Section>
            <Section heading="Your task">
              <Bullets ordered items={content.task(facts)} />
              <p className="mt-2 text-[0.8125rem] text-ink-3">Best done as: {content.who}</p>
            </Section>
            <Section heading="Where to go">
              <div className="flex flex-wrap gap-3">
                {content.where.map((w) => (
                  <Link key={w.to} to={w.to} className="font-medium text-accent-ink hover:underline">
                    {w.label} →
                  </Link>
                ))}
              </div>
            </Section>
            <Section heading="What to inspect">
              <Bullets items={content.inspect(facts)} />
            </Section>
            <Section heading="What a correct result looks like">{content.expected(facts)}</Section>
            <Section heading="The concept">{content.concept}</Section>

            {content.tool === 'idempotency-lab' && <IdempotencyLab />}
            {content.tool === 'sample-statement' && <SampleStatement guide={guide} />}
            {content.tool === 'ai' && <AiTool />}

            <div className="rounded-md border border-border bg-surface-2 p-4">
              {content.answer && (
                <div className="mb-3 max-w-sm">
                  <label htmlFor={`answer-${content.id}`} className="field-label">
                    {content.answer.label}
                  </label>
                  <Input
                    id={`answer-${content.id}`}
                    className="mt-1.5"
                    value={answer}
                    inputMode={content.answer.inputMode}
                    placeholder={content.answer.placeholder}
                    onChange={(e) => setAnswer(e.target.value)}
                  />
                </div>
              )}
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={check}
                  disabled={verify.isPending || !canVerify || (Boolean(content.answer) && answer.trim() === '')}
                >
                  {verify.isPending ? 'Checking…' : 'Check my work'}
                </Button>
                <span className="text-[0.8125rem] text-ink-3">
                  {state.kind === 'answer'
                    ? 'Checked against the live data - not against a stored answer.'
                    : 'Checked against the real state of the books.'}
                </span>
              </div>
              {!canVerify && (
                <p className="mt-2 text-[0.8125rem] text-warning">
                  This role is read-only, so it cannot record progress. You can still read and try everything it is
                  allowed to see.
                </p>
              )}
              <div aria-live="polite" className="mt-3">
                {result && (
                  <p
                    className={cn(
                      'rounded-sm border px-3 py-2 text-[0.8125rem]',
                      result.passed
                        ? 'border-positive/30 bg-positive-soft text-positive'
                        : 'border-warning/30 bg-warning-soft text-warning',
                    )}
                  >
                    <strong>{result.passed ? 'Passed. ' : 'Not yet. '}</strong>
                    {result.message}
                  </p>
                )}
              </div>
            </div>
          </div>
        )}
      </Panel>
    </li>
  );
}

function People({ guide }: { guide: Guide }) {
  const { user } = useAuth();
  return (
    <Panel>
      <PanelHeader
        title="The people at Meridian"
        description="Sign out and in as a different person to see the role rules in action. All e-mails are synthetic."
      />
      <div className="overflow-x-auto">
        <table className="w-full text-left text-[0.8125rem]">
          <thead className="text-2xs uppercase tracking-[0.05em] text-ink-3">
            <tr>
              <th className="px-4 py-2 font-semibold">Person</th>
              <th className="px-4 py-2 font-semibold">Role</th>
              <th className="px-4 py-2 font-semibold">What they do</th>
              <th className="px-4 py-2 font-semibold">Login</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {guide.personas.map((p) => (
              <tr key={p.key}>
                <td className="px-4 py-2.5">
                  <div className="font-medium text-ink">
                    {p.full_name} {user?.email === p.email && <Badge tone="accent">You</Badge>}
                  </div>
                  <div className="num text-ink-3">{p.email}</div>
                </td>
                <td className="px-4 py-2.5">
                  <Badge tone="neutral">{p.role}</Badge>
                  <div className="mt-0.5 text-ink-3">{p.title}</div>
                </td>
                <td className="px-4 py-2.5 text-ink-2">{p.responsibilities}</td>
                <td className="px-4 py-2.5">
                  {p.active ? (
                    <Badge tone="positive">Ready</Badge>
                  ) : (
                    <span className="text-ink-3">Not set up - see “Create logins” in the docs</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export default function LearningGuidePage() {
  const isDemo = useIsDemo();
  const guide = useGuide();

  const header = (
    <PageHeader
      title="Demo learning guide"
      description="Learn LEDGR by operating a fictional company - twelve exercises, each checked against the real books."
    />
  );

  if (!isDemo) {
    return (
      <>
        {header}
        <EmptyState
          icon={<GraduationCap className="h-4 w-4" />}
          title="This organization has no learning guide"
          description="The guide belongs to the fictional Meridian Commerce demonstration. Sign in as one of its people to use it."
        />
      </>
    );
  }

  if (guide.isLoading) {
    return (
      <>
        {header}
        <div className="space-y-3 px-5 py-6 sm:px-8">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      </>
    );
  }

  if (guide.error || !guide.data) {
    const notDemo = guide.error instanceof ApiError && guide.error.status === 404;
    return (
      <>
        {header}
        {notDemo ? (
          <EmptyState title="No guide for this organization" />
        ) : (
          <ErrorState error={guide.error} onRetry={() => guide.refetch()} context="the learning guide" />
        )}
      </>
    );
  }

  const g = guide.data;
  const byId = new Map(g.exercises.map((e) => [e.id, e]));
  const percent = Math.round((g.completed_count / g.total_count) * 100);

  return (
    <>
      {header}
      <div className="space-y-5 px-5 py-6 sm:px-8">
        <Panel>
          <div className="flex flex-wrap items-start gap-4 px-4 py-4">
            <FlaskConical className="mt-0.5 h-5 w-5 text-warning" aria-hidden />
            <div className="min-w-0 flex-1 text-[0.875rem] leading-relaxed text-ink-2">
              <p className="font-semibold text-ink">{g.organization_name} - a fictional demonstration</p>
              <p className="mt-1">
                Meridian is an invented Indian B2B trading company: it buys industrial supplies on credit, sells to
                business customers on credit, collects and pays by bank transfer, and reconciles its operating bank
                account against a synthetic statement. Amounts are in rupees. Everything - companies, people,
                references, bank records - is made up; there is no real bank connection and no real money.
              </p>
              <p className="mt-1">
                Posting above {`₹${formatMinor(g.approval_threshold_minor, 'INR')}`} needs a second person. The history
                runs to {formatDate(g.as_of)}. LEDGR holds values, not stock quantities, and does not compute tax - the
                GST lines are illustrative arithmetic in the seed.
              </p>
            </div>
          </div>
          <div className="border-t border-border px-4 py-3">
            <div className="flex items-center justify-between text-[0.8125rem]">
              <span className="font-medium text-ink">
                Your progress: {g.completed_count} of {g.total_count} exercises
              </span>
              <span className="num text-ink-3">{percent}%</span>
            </div>
            <div
              className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-3"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={g.total_count}
              aria-valuenow={g.completed_count}
              aria-label="Exercises completed"
            >
              <div className="h-full bg-accent transition-all" style={{ width: `${percent}%` }} />
            </div>
          </div>
        </Panel>

        <People guide={g} />

        <ol className="space-y-3" aria-label="Exercises">
          {EXERCISES.map((content, i) => {
            const state = byId.get(content.id);
            if (!state) return null;
            return <ExerciseCard key={content.id} index={i} content={content} state={state} guide={g} />;
          })}
        </ol>
      </div>
    </>
  );
}
