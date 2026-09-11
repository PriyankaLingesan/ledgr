/**
 * Ledger explorer.
 *
 * Presented in the traditional two-money-column form (debit | credit) so an
 * accountant reads it the way they expect, with an auditor's filters on top:
 * account, direction, currency, date window, amount floor and free text across
 * the originating transaction.
 */

import { Search } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { AccountRef, Amount, DirectionBadge } from '../components/ledger/atoms';
import { Button, Input, Panel, Select } from '../components/ui/primitives';
import {
  EmptyState,
  ErrorState,
  Pagination,
  Table,
  TableScroller,
  TableSkeleton,
  THead,
} from '../components/ui/Table';
import { formatDateTime, formatInteger } from '../lib/format';
import { useDebounced } from '../lib/hooks';
import { useAccounts, useCurrencies, useEntries } from '../lib/queries';

const LIMIT = 50;

export default function LedgerPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const [search, setSearch] = useState('');
  const [accountId, setAccountId] = useState(params.get('account_id') ?? '');
  const [direction, setDirection] = useState('');
  const [currency, setCurrency] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [minAmount, setMinAmount] = useState('');
  const [offset, setOffset] = useState(0);

  const debouncedSearch = useDebounced(search);
  const debouncedMin = useDebounced(minAmount);
  const { data: currencies } = useCurrencies();
  const accountsQuery = useAccounts({ limit: 200, offset: 0 });
  const transactionId = params.get('transaction_id') ?? undefined;

  const query = useEntries({
    q: debouncedSearch || undefined,
    account_id: accountId || undefined,
    transaction_id: transactionId,
    direction: direction || undefined,
    currency: currency || undefined,
    created_from: from ? new Date(from).toISOString() : undefined,
    created_to: to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
    min_amount_minor: debouncedMin ? Number(debouncedMin) : undefined,
    limit: LIMIT,
    offset,
  });

  const reset = () => setOffset(0);
  const hasFilters = Boolean(
    search || accountId || direction || currency || from || to || minAmount || transactionId,
  );

  return (
    <>
      <PageHeader
        title="Ledger explorer"
        description={
          query.data
            ? `${formatInteger(query.data.total)} entries match the current filters`
            : 'Every debit and credit ever posted'
        }
      />

      <div className="px-5 py-6 sm:px-8">
        <Panel>
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
            <div className="relative min-w-52 flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-3" />
              <Input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  reset();
                }}
                placeholder="Search transaction reference or description"
                className="pl-8"
              />
            </div>
            <Select
              value={accountId}
              onChange={(event) => {
                setAccountId(event.target.value);
                reset();
              }}
              className="w-56 num"
            >
              <option value="">All accounts</option>
              {(accountsQuery.data?.items ?? []).map((account) => (
                <option key={account.id} value={account.id}>
                  {account.code}
                </option>
              ))}
            </Select>
            <Select
              value={direction}
              onChange={(event) => {
                setDirection(event.target.value);
                reset();
              }}
              className="w-32"
            >
              <option value="">Both sides</option>
              <option value="DEBIT">Debits</option>
              <option value="CREDIT">Credits</option>
            </Select>
            <Select
              value={currency}
              onChange={(event) => {
                setCurrency(event.target.value);
                reset();
              }}
              className="w-28"
            >
              <option value="">Currency</option>
              {(currencies ?? []).map((option) => (
                <option key={option.code} value={option.code}>
                  {option.code}
                </option>
              ))}
            </Select>
            <Input
              type="date"
              value={from}
              onChange={(event) => {
                setFrom(event.target.value);
                reset();
              }}
              className="w-36"
              title="Posted from"
            />
            <Input
              type="date"
              value={to}
              onChange={(event) => {
                setTo(event.target.value);
                reset();
              }}
              className="w-36"
              title="Posted to"
            />
            <Input
              value={minAmount}
              onChange={(event) => {
                setMinAmount(event.target.value.replace(/\D/g, ''));
                reset();
              }}
              placeholder="Min minor units"
              className="num w-36 text-right"
              title="Minimum amount in minor units"
            />
            {hasFilters && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch('');
                  setAccountId('');
                  setDirection('');
                  setCurrency('');
                  setFrom('');
                  setTo('');
                  setMinAmount('');
                  setParams({});
                  reset();
                }}
              >
                Clear
              </Button>
            )}
          </div>

          {transactionId && (
            <p className="border-b border-border bg-accent-soft px-4 py-2 text-xs text-accent-ink">
              Filtered to a single transaction ·{' '}
              <Link to={`/app/transactions/${transactionId}`} className="num underline">
                open transaction
              </Link>
            </p>
          )}

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} />
          ) : query.isLoading ? (
            <TableSkeleton rows={12} columns={7} />
          ) : query.data && query.data.items.length === 0 ? (
            <EmptyState
              title="No ledger entries match"
              description="Entries are never deleted, so an empty result only ever means the filters are too narrow."
            />
          ) : (
            <>
              <TableScroller>
                <Table>
                  <THead>
                    <th className="th">Seq</th>
                    <th className="th">Posted</th>
                    <th className="th">Account</th>
                    <th className="th">Transaction</th>
                    <th className="th">Dir</th>
                    <th className="th text-right">Debit</th>
                    <th className="th text-right">Credit</th>
                    <th className="th">Memo</th>
                  </THead>
                  <tbody>
                    {query.data?.items.map((entry) => (
                      <tr
                        key={entry.id}
                        className="row-link"
                        onClick={() => navigate(`/app/transactions/${entry.transaction_id}`)}
                      >
                        <td className="td num text-ink-3">{entry.seq}</td>
                        <td className="td whitespace-nowrap text-ink-3">
                          {formatDateTime(entry.created_at)}
                        </td>
                        <td className="td">
                          <AccountRef account={entry.account} />
                        </td>
                        <td className="td">
                          <span className="num text-ink">{entry.transaction?.reference}</span>
                          <span className="block max-w-[18rem] truncate text-xs text-ink-3">
                            {entry.transaction?.description}
                          </span>
                        </td>
                        <td className="td">
                          <DirectionBadge direction={entry.direction} />
                        </td>
                        <td className="td text-right">
                          {entry.direction === 'DEBIT' ? (
                            <Amount money={entry.amount} tone="debit" />
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                        <td className="td text-right">
                          {entry.direction === 'CREDIT' ? (
                            <Amount money={entry.amount} tone="credit" />
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                        <td className="td max-w-[14rem] truncate text-ink-3">
                          {entry.memo ?? '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </TableScroller>
              <Pagination
                total={query.data?.total ?? 0}
                limit={LIMIT}
                offset={offset}
                onChange={setOffset}
                unit="entries"
              />
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
