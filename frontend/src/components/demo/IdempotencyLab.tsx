import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { ApiError, newIdempotencyKey, requestDetailed } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { useAccounts } from '../../lib/queries';
import { Badge, Button } from '../ui/primitives';

interface LabEntry {
  label: string;
  status: number;
  replayed: boolean;
  detail: string;
}

const AMOUNT_MINOR = 100_000; // ₹1,000.00
const LAB_NOTE = 'Idempotency lab: petty cash top-up';

/**
 * A safe sandbox for exercise 4. It sends *real* postings to the real API with a real
 * Idempotency-Key - the point is to see the server's behaviour, not a simulation of it.
 */
export function IdempotencyLab() {
  const { can } = useAuth();
  const client = useQueryClient();
  const accounts = useAccounts({ limit: 200 });
  const [key] = useState(() => `lab-${newIdempotencyKey().slice(0, 8)}`);
  const [log, setLog] = useState<LabEntry[]>([]);
  const [busy, setBusy] = useState(false);

  const petty = accounts.data?.items.find((a) => a.code === 'CASH.PETTY');
  const bank = accounts.data?.items.find((a) => a.code === 'BANK.DNB.OPS');
  const allowed = can('transactions:post');

  async function send(label: string, amountMinor: number) {
    if (!petty || !bank) return;
    setBusy(true);
    try {
      const res = await requestDetailed<{ reference: string }>('/transactions', {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: {
          description: LAB_NOTE,
          currency: 'INR',
          entries: [
            { account_id: petty.id, direction: 'DEBIT', amount_minor: amountMinor },
            { account_id: bank.id, direction: 'CREDIT', amount_minor: amountMinor },
          ],
        },
      });
      setLog((prev) => [
        ...prev,
        {
          label,
          status: res.status,
          replayed: res.replayed,
          detail: res.replayed
            ? `Answered from the stored response: still ${res.data.reference}, nothing posted again.`
            : `Posted ${res.data.reference}.`,
        },
      ]);
    } catch (error) {
      const e = error instanceof ApiError ? error : null;
      setLog((prev) => [
        ...prev,
        {
          label,
          status: e?.status ?? 0,
          replayed: false,
          detail: e ? `${e.code}: ${e.message}` : 'The request failed.',
        },
      ]);
    } finally {
      setBusy(false);
      void client.invalidateQueries({ queryKey: ['transactions'] });
      void client.invalidateQueries({ queryKey: ['demo', 'guide'] });
    }
  }

  return (
    <div className="rounded-md border border-border bg-surface-2 p-4" data-testid="idempotency-lab">
      <p className="text-[0.875rem] font-semibold text-ink">Idempotency lab</p>
      <p className="mt-1 text-[0.8125rem] text-ink-2">
        Each button sends a real request to the ledger with the key <code className="num">{key}</code> (a ₹1,000
        petty-cash top-up from the bank).
      </p>
      {!allowed && (
        <p className="mt-2 text-[0.8125rem] text-warning">
          Your role cannot post transactions. Sign in as Kavya (Finance) or the owner to use the lab.
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="primary" size="sm" disabled={!allowed || busy || !petty || !bank} onClick={() => send('1. Send the posting', AMOUNT_MINOR)}>
          1. Send the posting
        </Button>
        <Button size="sm" disabled={!allowed || busy || !petty || !bank} onClick={() => send('2. Send the same request again', AMOUNT_MINOR)}>
          2. Send the exact same request again
        </Button>
        <Button size="sm" disabled={!allowed || busy || !petty || !bank} onClick={() => send('3. Same key, different amount', AMOUNT_MINOR * 2)}>
          3. Same key, different amount
        </Button>
      </div>
      {log.length > 0 && (
        <ol className="mt-3 space-y-1.5 text-[0.8125rem]" aria-label="Lab results">
          {log.map((row, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2">
              <span className="text-ink-2">{row.label}</span>
              <Badge tone={row.status >= 200 && row.status < 300 ? 'positive' : 'negative'} mono>
                {row.status}
              </Badge>
              {row.replayed && <Badge tone="accent">Replay</Badge>}
              <span className="text-ink">{row.detail}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
