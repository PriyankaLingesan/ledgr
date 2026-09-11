/** Table shell, states and pagination shared by every listing screen. */

import { AlertTriangle, ChevronLeft, ChevronRight, Inbox } from 'lucide-react';
import type { ReactNode } from 'react';

import { ApiError } from '../../lib/api';
import { cn } from '../../lib/cn';
import { formatInteger } from '../../lib/format';
import { Button, Skeleton } from './primitives';

export function TableScroller({ children, className }: { children: ReactNode; className?: string }) {
  // Wide financial tables scroll inside their own panel; the page never does.
  return <div className={cn('w-full overflow-x-auto', className)}>{children}</div>;
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return <table className={cn('w-full border-collapse text-left', className)}>{children}</table>;
}

export function THead({ children }: { children: ReactNode }) {
  return (
    <thead className="bg-surface-2">
      <tr className="border-b border-border">{children}</tr>
    </thead>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2.5 px-6 py-16 text-center">
      <div className="mb-1 flex h-9 w-9 items-center justify-center rounded-md border border-border bg-surface-2 text-ink-3">
        {icon ?? <Inbox className="h-4 w-4" strokeWidth={1.6} />}
      </div>
      <p className="text-[0.9375rem] font-medium text-ink">{title}</p>
      {description && <p className="max-w-sm text-[0.8125rem] leading-relaxed text-ink-2">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  context = 'this',
}: {
  error: unknown;
  onRetry?: () => void;
  /** e.g. "transactions" -> "We couldn't load transactions right now." */
  context?: string;
}) {
  const api = error instanceof ApiError ? error : null;
  const humanMessage =
    api && api.status >= 400 && api.status < 500 && api.status !== 404
      ? api.message
      : `We couldn't load ${context} right now. Please try again.`;

  return (
    <div className="flex flex-col items-center justify-center gap-2.5 px-6 py-14 text-center">
      <div className="flex h-9 w-9 items-center justify-center rounded-md border border-negative/30 bg-negative-soft text-negative">
        <AlertTriangle className="h-4 w-4" strokeWidth={1.8} />
      </div>
      <p className="text-[0.9375rem] font-medium text-ink">{humanMessage}</p>
      {(api?.code || api?.requestId) && (
        <div className="flex items-center gap-2 text-2xs text-ink-3">
          {api?.code && (
            <span className="num rounded-xs border border-border bg-surface-2 px-1.5 py-px">
              {api.code}
            </span>
          )}
          {api?.requestId && <span className="num">request {api.requestId.slice(0, 12)}</span>}
        </div>
      )}
      {onRetry && (
        <Button size="sm" className="mt-2" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function TableSkeleton({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="divide-y divide-border">
      {Array.from({ length: rows }).map((_, rowIndex) => (
        <div key={rowIndex} className="flex items-center gap-4 px-3 py-2.5">
          {Array.from({ length: columns }).map((__, columnIndex) => (
            <Skeleton
              key={columnIndex}
              className={cn('h-3', columnIndex === 0 ? 'w-40' : columnIndex === 1 ? 'w-56' : 'w-20')}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function Pagination({
  total,
  limit,
  offset,
  onChange,
  unit = 'rows',
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
  unit?: string;
}) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + limit, total);
  const canPrevious = offset > 0;
  const canNext = to < total;

  return (
    <div className="flex items-center justify-between gap-4 border-t border-border px-3 py-2">
      <p className="text-xs text-ink-3">
        <span className="num text-ink-2">{formatInteger(from)}</span>–
        <span className="num text-ink-2">{formatInteger(to)}</span> of{' '}
        <span className="num text-ink-2">{formatInteger(total)}</span> {unit}
      </p>
      <div className="flex items-center gap-1">
        <Button
          size="sm"
          variant="ghost"
          disabled={!canPrevious}
          onClick={() => onChange(Math.max(0, offset - limit))}
          icon={<ChevronLeft className="h-3.5 w-3.5" />}
        >
          Previous
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!canNext}
          onClick={() => onChange(offset + limit)}
        >
          Next
          <ChevronRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}
