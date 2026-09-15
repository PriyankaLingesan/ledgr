/**
 * LEDGR Intelligence.
 *
 * Two independent capabilities, not one ambiguous chat box: asking a
 * question about the real ledger, and drafting a transaction proposal from a
 * description. Keeping them separate means the backend never has to guess
 * which the operator meant, and the operator always knows which endpoint -
 * and which guarantees - apply to what they just typed.
 */

import { PageHeader } from '../components/layout/AppShell';
import { AIUnavailableNotice } from '../components/ai/AIUnavailableNotice';
import { AskLedgerPanel } from '../components/ai/AskLedgerPanel';
import { TransactionAssistantPanel } from '../components/ai/TransactionAssistantPanel';
import { useAIStatus } from '../lib/queries';

export default function IntelligencePage() {
  const status = useAIStatus();
  const available = status.data?.available ?? false;

  return (
    <>
      <PageHeader
        title="LEDGR Intelligence"
        description="Ask about your ledger, or describe a transaction and review a draft before posting."
      />

      <div className="space-y-4 px-5 py-6 sm:px-8">
        {!status.isLoading && !available && (
          <AIUnavailableNotice reason={status.data?.reason} />
        )}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <AskLedgerPanel available={available} />
          <TransactionAssistantPanel available={available} />
        </div>

        <p className="text-[0.8125rem] text-ink-3">
          AI suggestions are drafts only. Every posting - AI-assisted or not - still passes
          through LEDGR's normal balance validation, atomic posting, and idempotency handling.
          The ledger itself is never written to directly by AI.
        </p>
      </div>
    </>
  );
}
