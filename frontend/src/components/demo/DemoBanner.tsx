import { FlaskConical } from 'lucide-react';
import { Link } from 'react-router-dom';

import { useAuth } from '../../lib/auth';
import { useIsDemo } from '../../lib/demo';

/**
 * Shown on every console page of a demonstration organization, so nobody mistakes it for a real
 * company. Renders nothing for a real organization.
 */
export function DemoBanner() {
  const isDemo = useIsDemo();
  const { user } = useAuth();
  if (!isDemo) return null;
  return (
    <div
      role="note"
      aria-label="Fictional demonstration"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-warning/30 bg-warning-soft px-5 py-2 text-[0.8125rem] text-warning sm:px-8"
    >
      <span className="inline-flex items-center gap-1.5 font-semibold uppercase tracking-[0.05em]">
        <FlaskConical className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
        Fictional demonstration
      </span>
      <span className="text-ink-2">
        {user?.organization_name ?? 'This organization'} is synthetic: no real company, bank or customer, and
        no real money moves.
      </span>
      <Link to="/app/learn" className="ml-auto font-medium text-accent-ink hover:underline">
        Open the learning guide
      </Link>
    </div>
  );
}
