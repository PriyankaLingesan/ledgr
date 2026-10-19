/**
 * Financial Flight Recorder.
 *
 * LEDGR's ledger already proves a balance is *correct* (invariants I1-I13).
 * This page is what makes it *explainable*: pick an account, ask why its
 * balance is what it is, reconstruct what it was at any past instant, and
 * compare two points in time - all reconstructed live from the same
 * append-only `ledger_entries` table everything else in the app reads, never
 * a second source of truth.
 */

import { CalendarClock, GitCompare, SearchCheck, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { ControlExceptionsPanel } from '../components/investigation/ControlExceptionsPanel';
import { BalanceChangePanel } from '../components/investigation/BalanceChangePanel';
import { DuplicateFlagsPanel } from '../components/investigation/DuplicateFlagsPanel';
import { IntegrityMonitorPanel } from '../components/investigation/IntegrityMonitorPanel';
import { PageHeader } from '../components/layout/AppShell';
import { Amount } from '../components/ledger/atoms';
import {
  Button,
  Field,
  Input,
  Panel,
  PanelHeader,
  Select,
  Skeleton,
  Spinner,
} from '../components/ui/primitives';
import { ErrorState } from '../components/ui/Table';
import { useAuth } from '../lib/auth';
import { formatDateTime } from '../lib/format';
import {
  useAccountBalance,
  useAccountBalanceAt,
  useAccountBalanceChange,
  useAccounts,
  useCreateInvestigation,
} from '../lib/queries';

/** `datetime-local` has no timezone of its own; treat it as the browser's local time. */
function localInputToIso(value: string): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function nowForInput(): string {
  const now = new Date();
  now.setSeconds(0, 0);
  return now.toISOString().slice(0, 16);
}

export default function FlightRecorderPage() {
  const navigate = useNavigate();
  const accounts = useAccounts({ limit: 100 });
  const [accountId, setAccountId] = useState<string>('');

  const [explainOpen, setExplainOpen] = useState(false);

  const [historicalAt, setHistoricalAt] = useState('');
  const [historicalSubmittedAt, setHistoricalSubmittedAt] = useState<string | undefined>();

  const [compareFrom, setCompareFrom] = useState('');
  const [compareTo, setCompareTo] = useState(nowForInput());
  const [compareParams, setCompareParams] = useState<{ from?: string; to?: string } | null>(null);

  const balance = useAccountBalance(accountId || undefined);
  const explain = useAccountBalanceChange(explainOpen ? accountId : undefined);
  const historicalBalance = useAccountBalanceAt(accountId || undefined, historicalSubmittedAt);
  const compare = useAccountBalanceChange(
    compareParams ? accountId : undefined,
    compareParams ?? {},
  );

  const createCase = useCreateInvestigation();
  const { can } = useAuth();
  const selectedAccount = accounts.data?.items.find((a) => a.id === accountId);

  const investigateDiscrepancy = () => {
    if (!accountId || !selectedAccount) return;
    createCase.mutate(
      {
        title: `Discrepancy on ${selectedAccount.code}`,
        issue_summary: `Opened from the Flight Recorder while reviewing ${selectedAccount.code} — ${selectedAccount.name}.`,
        source: 'FLIGHT_RECORDER',
        account_id: accountId,
        window_from: explain.data?.from ?? undefined,
        window_to: explain.data?.to ?? undefined,
      },
      { onSuccess: (created) => navigate(`/app/investigations/${created.id}`) },
    );
  };

  return (
    <>
      <PageHeader
        title="Financial Flight Recorder"
        description="Trace any balance back to the transactions and ledger entries that produced it."
      />

      <div className="space-y-4 px-5 py-6 sm:px-8">
        <Panel>
          <PanelHeader title="Choose an account" />
          <div className="flex flex-wrap items-end gap-4 p-4">
            <Field label="Account" className="w-64" htmlFor="fr-account">
              <Select
                id="fr-account"
                value={accountId}
                onChange={(event) => {
                  setAccountId(event.target.value);
                  setExplainOpen(false);
                  setHistoricalSubmittedAt(undefined);
                  setCompareParams(null);
                }}
              >
                <option value="">Select an account…</option>
                {accounts.data?.items.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.code} — {account.name}
                  </option>
                ))}
              </Select>
            </Field>

            {accountId && (
              <div className="pb-1.5">
                <p className="caption">Current balance</p>
                {balance.isLoading ? (
                  <Skeleton className="mt-1 h-7 w-32" />
                ) : (
                  <Amount money={balance.data?.balance} showCurrency tone="auto" className="text-xl" />
                )}
              </div>
            )}

            {accountId && (
              <Button
                variant="primary"
                icon={<Sparkles className="h-3.5 w-3.5" />}
                onClick={() => setExplainOpen(true)}
              >
                Explain this balance
              </Button>
            )}

            {accountId && can('investigations:write') && (
              <Button
                icon={
                  createCase.isPending ? (
                    <Spinner />
                  ) : (
                    <SearchCheck className="h-3.5 w-3.5" strokeWidth={1.8} />
                  )
                }
                disabled={createCase.isPending}
                onClick={investigateDiscrepancy}
              >
                Investigate discrepancy
              </Button>
            )}
          </div>
        </Panel>

        {!accountId ? (
          <Panel>
            <p className="px-4 py-8 text-center text-[0.875rem] text-ink-3">
              Select an account above to start investigating.
            </p>
          </Panel>
        ) : (
          <>
            {explainOpen && (
              <Panel>
                <PanelHeader
                  title="Why is this the balance?"
                  description="Opening balance, every event since, and the resulting current balance - last 30 days by default."
                />
                {explain.isError ? (
                  <ErrorState error={explain.error} onRetry={() => explain.refetch()} />
                ) : (
                  <BalanceChangePanel change={explain.data} isLoading={explain.isLoading} />
                )}
              </Panel>
            )}

            <Panel>
              <PanelHeader
                title="Historical balance"
                description="Reconstructed from ledger entries as of a specific instant - never a snapshot table."
              />
              <div className="flex flex-wrap items-end gap-3 border-b border-border p-4">
                <Field label="As of" htmlFor="fr-hist-at" className="w-64">
                  <Input
                    id="fr-hist-at"
                    type="datetime-local"
                    value={historicalAt}
                    onChange={(event) => setHistoricalAt(event.target.value)}
                  />
                </Field>
                <Button
                  icon={<CalendarClock className="h-3.5 w-3.5" />}
                  disabled={!historicalAt}
                  onClick={() => setHistoricalSubmittedAt(localInputToIso(historicalAt))}
                >
                  Look up balance
                </Button>
              </div>
              {historicalSubmittedAt &&
                (historicalBalance.isLoading ? (
                  <Skeleton className="m-4 h-10 w-48" />
                ) : historicalBalance.isError ? (
                  <ErrorState error={historicalBalance.error} context="the historical balance" />
                ) : (
                  historicalBalance.data && (
                    <div className="px-4 py-4">
                      <p className="caption">
                        Balance as of {formatDateTime(historicalBalance.data.as_of)}
                      </p>
                      <Amount
                        money={historicalBalance.data.balance}
                        showCurrency
                        tone="auto"
                        className="mt-1 text-2xl"
                      />
                      <p className="mt-1 text-[0.8125rem] text-ink-3">
                        Derived from {historicalBalance.data.entry_count} entries posted at or
                        before that instant.
                      </p>
                    </div>
                  )
                ))}
            </Panel>

            <Panel>
              <PanelHeader
                title="Compare two points in time"
                description="Also answers 'why did my balance change' - opening, closing, net change, and every transaction responsible."
              />
              <div className="flex flex-wrap items-end gap-3 border-b border-border p-4">
                <Field label="From" htmlFor="fr-cmp-from">
                  <Input
                    id="fr-cmp-from"
                    type="datetime-local"
                    value={compareFrom}
                    onChange={(event) => setCompareFrom(event.target.value)}
                  />
                </Field>
                <Field label="To" htmlFor="fr-cmp-to">
                  <Input
                    id="fr-cmp-to"
                    type="datetime-local"
                    value={compareTo}
                    onChange={(event) => setCompareTo(event.target.value)}
                  />
                </Field>
                <Button
                  icon={<GitCompare className="h-3.5 w-3.5" />}
                  disabled={!compareFrom || !compareTo}
                  onClick={() =>
                    setCompareParams({
                      from: localInputToIso(compareFrom),
                      to: localInputToIso(compareTo),
                    })
                  }
                >
                  Compare
                </Button>
              </div>
              {compareParams &&
                (compare.isError ? (
                  <ErrorState error={compare.error} onRetry={() => compare.refetch()} />
                ) : (
                  <BalanceChangePanel change={compare.data} isLoading={compare.isLoading} />
                ))}
            </Panel>
          </>
        )}

        <ControlExceptionsPanel />

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <DuplicateFlagsPanel />
          <IntegrityMonitorPanel />
        </div>
      </div>
    </>
  );
}
