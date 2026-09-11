import { cn } from '../../lib/cn';

/**
 * The LEDGR mark: two triangles meeting on a diagonal, ink and accent -
 * balance, plainly stated. No monogram, no abstract swoosh.
 */
export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 28 28" className={cn('shrink-0', className)} aria-hidden>
      <rect x="0.5" y="0.5" width="27" height="27" rx="6" className="fill-surface stroke-border" />
      <path d="M6 20 L6 8 L20 8 Z" className="fill-ink" />
      <path d="M8 20 L22 20 L22 8 Z" className="fill-accent" />
    </svg>
  );
}

export function Wordmark({ className, tagline }: { className?: string; tagline?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2.5', className)}>
      <Mark className="h-7 w-7" />
      <span className="leading-tight">
        <span className="block text-[1.0625rem] font-semibold tracking-[0.01em] text-ink">
          LEDGR
        </span>
        {tagline && <span className="block text-2xs text-ink-3">{tagline}</span>}
      </span>
    </span>
  );
}
