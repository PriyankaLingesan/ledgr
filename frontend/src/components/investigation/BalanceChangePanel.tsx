/**
 * The Financial Flight Recorder's core view: opening balance, every event
 * that happened in a window, and the closing balance those events produce.
 *
 * One component, three UI framings on the page that renders it: "Explain
 * This Balance" (default window), "why did my balance change" and "compare
 * two points in time" (explicit `from`/`to`) all render this same shape,
 * because they're the same query - `GET /accounts/{id}/balance-change` -
 * with different default parameters. Nothing here is fabricated: every event
 * links to the real transaction it came from.
 */

import { CheckCircle2, ChevronRight, Play, RotateCcw, ShieldAlert } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { Amount, DirectionBadge } from '../ledger/atoms';
import { Badge, Button, Skeleton } from '../ui/primitives';
import { EmptyState } from '../ui/Table';
import { cn } from '../../lib/cn';
import { formatDate, formatDateTime } from '../../lib/format';
import type { BalanceChange, InvestigationEvent } from '../../lib/types';

function groupByDay(events: InvestigationEvent[]): [string, InvestigationEvent[]][] {
  const groups = new Map<string, InvestigationEvent[]>();
  for (const event of events) {
    const day = formatDate(event.created_at);
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day)!.push(event);
  }
  return [...groups.entries()];
}

function EventRow({ event }: { event: InvestigationEvent }) {
  const positive = event.effect.amount_minor >= 0;
  return (
    <Link
      to={`/app/transactions/${event.transaction_id}`}
      className="row-link flex items-center justify-between gap-3 px-4 py-2.5"
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="num text-[0.8125rem] font-medium text-ink">{event.reference}</span>
          <DirectionBadge direction={event.direction} />
          {event.transaction_status === 'REVERSED' && <Badge tone="warning">Reversed</Badge>}
          {event.transaction_kind === 'REVERSAL' && <Badge tone="accent">Reversal</Badge>}
        </div>
        <p className="mt-0.5 truncate text-[0.8125rem] text-ink-3">{event.description}</p>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <div className="text-right">
          <Amount
            money={event.effect}
            signed
            tone={positive ? 'credit' : 'debit'}
            className="block"
          />
          <span className="num text-2xs text-ink-3">{formatDateTime(event.created_at)}</span>
        </div>
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-3" />
      </div>
    </Link>
  );
}

function PlayForward({ change }: { change: BalanceChange }) {
  const [step, setStep] = useState(-1); // -1 = not playing
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  const start = () => {
    setStep(0);
    timer.current = setInterval(() => {
      setStep((s) => {
        if (s + 1 >= change.events.length) {
          if (timer.current) clearInterval(timer.current);
          return change.events.length; // land on the final, closing state
        }
        return s + 1;
      });
    }, 700);
  };

  const stop = () => {
    if (timer.current) clearInterval(timer.current);
    setStep(-1);
  };

  if (change.events.length === 0) return null;

  const playing = step >= 0;
  const currentEvent = playing && step < change.events.length ? change.events[step] : null;
  const currentBalance =
    step < 0
      ? change.opening_balance
      : step >= change.events.length
        ? change.closing_balance
        : change.events[step].running_balance;

  return (
    <div className="border-t border-border px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[0.8125rem] text-ink-3">
          Replays this window's own events, step by step. This never changes the ledger - it only
          plays back numbers already fetched.
        </p>
        {!playing ? (
          <Button size="sm" icon={<Play className="h-3.5 w-3.5" />} onClick={start}>
            Play forward
          </Button>
        ) : (
          <Button size="sm" icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={stop}>
            Reset
          </Button>
        )}
      </div>
      {playing && (
        <div className="mt-3 flex items-center justify-between rounded-sm border border-border bg-surface-2 px-4 py-3">
          <div>
            <p className="caption">
              {currentEvent ? formatDate(currentEvent.created_at) : 'Final balance'}
            </p>
            <p className="mt-0.5 text-[0.8125rem] text-ink-2">
              {currentEvent ? currentEvent.description : 'All events applied'}
            </p>
          </div>
          <Amount money={currentBalance} showCurrency tone="auto" className="text-xl" />
        </div>
      )}
    </div>
  );
}

export function BalanceChangePanel({
  change,
  isLoading,
  showPlayback = true,
}: {
  change: BalanceChange | undefined;
  isLoading: boolean;
  showPlayback?: boolean;
}) {
  if (isLoading) {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (!change) return null;

  const days = groupByDay(change.events);

  return (
    <div>
      <div className="grid grid-cols-3 divide-x divide-border border-b border-border">
        <div className="px-4 py-3.5">
          <p className="caption">Opening balance</p>
          <Amount money={change.opening_balance} showCurrency tone="auto" className="mt-1 text-lg" />
          <p className="mt-0.5 text-2xs text-ink-3">as of {formatDateTime(change.from)}</p>
        </div>
        <div className="px-4 py-3.5">
          <p className="caption">Net change</p>
          <Amount money={change.net_change} showCurrency signed tone="auto" className="mt-1 text-lg" />
          <p className="mt-0.5 text-2xs text-ink-3">
            {change.event_count} event{change.event_count === 1 ? '' : 's'}
          </p>
        </div>
        <div className="px-4 py-3.5">
          <p className="caption">Closing balance</p>
          <Amount money={change.closing_balance} showCurrency tone="auto" className="mt-1 text-lg" />
          <p className="mt-0.5 text-2xs text-ink-3">as of {formatDateTime(change.to)}</p>
        </div>
      </div>

      <div
        className={cn(
          'flex items-center gap-2 border-b border-border px-4 py-2 text-xs',
          change.reconciled ? 'text-positive' : 'text-negative',
        )}
      >
        {change.reconciled ? (
          <CheckCircle2 className="h-3.5 w-3.5" strokeWidth={2} />
        ) : (
          <ShieldAlert className="h-3.5 w-3.5" strokeWidth={2} />
        )}
        {change.reconciled
          ? 'Closing balance reconciles exactly with opening balance plus every event shown.'
          : 'These figures do not reconcile - this should never happen and needs investigation.'}
      </div>

      {change.events.length === 0 ? (
        <EmptyState
          title="No activity in this window"
          description="Nothing changed this account's balance in the selected period."
        />
      ) : (
        <div className="divide-y divide-border">
          {days.map(([day, events]) => (
            <div key={day}>
              <div className="bg-surface-2 px-4 py-1.5">
                <span className="caption">{day}</span>
              </div>
              <div className="divide-y divide-border">
                {events.map((event) => (
                  <EventRow key={event.entry_id} event={event} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {showPlayback && <PlayForward change={change} />}
    </div>
  );
}
