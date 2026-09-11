/**
 * Interface primitives.
 *
 * Deliberately small and square: 4-6px radii, hairline borders, 28-32px control
 * heights. The console is meant to be read at density, so nothing here spends
 * vertical space on decoration.
 */

import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react';

import { cn } from '../../lib/cn';

// --- buttons --------------------------------------------------------------

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
type ButtonSize = 'sm' | 'md';

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-accent-contrast border-accent hover:bg-accent-ink hover:border-accent-ink',
  secondary: 'bg-surface text-ink border-border-strong hover:bg-surface-3',
  ghost: 'bg-transparent text-ink-2 border-transparent hover:bg-surface-3 hover:text-ink',
  danger: 'bg-surface text-negative border-negative/40 hover:bg-negative-soft',
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-xs gap-1.5',
  md: 'h-8 px-3 text-[0.8125rem] gap-2',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
}

export function Button({
  variant = 'secondary',
  size = 'md',
  icon,
  className,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        'inline-flex items-center justify-center rounded-sm border font-medium transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-45',
        BUTTON_VARIANTS[variant],
        BUTTON_SIZES[size],
        className,
      )}
      {...props}
    >
      {icon}
      {children}
    </button>
  );
}

export function IconButton({
  className,
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cn(
        'inline-flex h-7 w-7 items-center justify-center rounded-sm border border-transparent',
        'text-ink-3 transition-colors hover:bg-surface-3 hover:text-ink',
        'disabled:cursor-not-allowed disabled:opacity-45',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}

// --- form controls --------------------------------------------------------

const CONTROL =
  'h-8 w-full rounded-sm border border-border bg-surface px-2.5 text-[0.8125rem] text-ink ' +
  'placeholder:text-ink-3 transition-colors hover:border-border-strong ' +
  'focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20 ' +
  'disabled:cursor-not-allowed disabled:bg-surface-3 disabled:text-ink-3';

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(CONTROL, className)} {...props} />;
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(CONTROL, 'cursor-pointer pr-7', className)} {...props}>
      {children}
    </select>
  );
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(CONTROL, 'h-auto min-h-16 py-2 leading-relaxed', className)} {...props} />;
}

export function Field({
  label,
  hint,
  error,
  required,
  htmlFor,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={htmlFor} className="label flex items-center gap-1">
        {label}
        {required && <span className="text-negative">*</span>}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-negative">{error}</p>
      ) : hint ? (
        <p className="text-xs text-ink-3">{hint}</p>
      ) : null}
    </div>
  );
}

// --- surfaces -------------------------------------------------------------

export function Panel({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn('panel', className)}>{children}</section>;
}

export function PanelHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header
      className={cn(
        'flex items-center justify-between gap-4 border-b border-border px-4 py-2.5',
        className,
      )}
    >
      <div className="min-w-0">
        <h2 className="truncate text-[0.8125rem] font-semibold tracking-tight text-ink">{title}</h2>
        {description && <p className="mt-0.5 truncate text-xs text-ink-3">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}

// --- indicators -----------------------------------------------------------

type Tone = 'neutral' | 'accent' | 'positive' | 'negative' | 'warning' | 'debit' | 'credit';

const TONES: Record<Tone, string> = {
  neutral: 'bg-surface-3 text-ink-2 border-border',
  accent: 'bg-accent-soft text-accent-ink border-accent/25',
  positive: 'bg-positive-soft text-positive border-positive/25',
  negative: 'bg-negative-soft text-negative border-negative/25',
  warning: 'bg-warning-soft text-warning border-warning/30',
  debit: 'bg-debit-soft text-debit-ink border-debit/25',
  credit: 'bg-credit-soft text-credit-ink border-credit/25',
};

export function Badge({
  tone = 'neutral',
  children,
  className,
  mono,
}: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  mono?: boolean;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-xs border px-1.5 py-px text-2xs font-semibold',
        'uppercase tracking-[0.05em] whitespace-nowrap',
        mono && 'font-mono',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function StatusDot({ tone = 'neutral', pulse }: { tone?: Tone; pulse?: boolean }) {
  const colour: Record<Tone, string> = {
    neutral: 'bg-ink-3',
    accent: 'bg-accent',
    positive: 'bg-positive',
    negative: 'bg-negative',
    warning: 'bg-warning',
    debit: 'bg-debit',
    credit: 'bg-credit',
  };
  return (
    <span className="relative inline-flex h-1.5 w-1.5 shrink-0">
      {pulse && (
        <span
          className={cn('absolute inline-flex h-full w-full animate-ping rounded-full opacity-60', colour[tone])}
        />
      )}
      <span className={cn('relative inline-flex h-1.5 w-1.5 rounded-full', colour[tone])} />
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-xs bg-surface-3', className)} />;
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cn('h-3.5 w-3.5 animate-spin text-ink-3', className)} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2" />
      <path d="M14.5 8A6.5 6.5 0 0 0 8 1.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
