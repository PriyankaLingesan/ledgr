import { Info } from 'lucide-react';

/** The one message every AI surface shows when no provider is configured. */
export function AIUnavailableNotice({ reason }: { reason?: string | null }) {
  return (
    <div className="flex items-start gap-2.5 rounded-md border border-border bg-surface-2 px-4 py-3 text-[0.8125rem] text-ink-2">
      <Info className="mt-0.5 h-4 w-4 shrink-0 text-ink-3" strokeWidth={1.7} />
      <span>
        {reason || 'AI features are unavailable because no AI provider is configured.'}
      </span>
    </div>
  );
}
