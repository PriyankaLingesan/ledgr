/** Transaction register. */

import { Plus, Search } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { Amount, TransactionStatusBadge } from '../components/ledger/atoms';
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
import { useCurrencies, useTransactions } from '../lib/queries';

const LIMIT = 25;

export default function TransactionsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [kind, setKind] = useState('');
  const [currency, setCurrency] = useState('');
  const [offset, setOffset] = useState(0);

  const debouncedSearch = useDebounced(search);
  const { data: currencies } = useCurrencies();

  const query = useTransactions({
    q: debouncedSearch || undefined,
    status: status || undefined,
    kind: kind || undefined,
    currency: currency || undefined,
    limit: LIMIT,
    offset,
  });

  const reset = () => setOffset(0);

  return (
    <>
      <PageHeader
        title="Transactions"
        description={
          query.data
            ? `${formatInteger(query.data.total)} transactions, newest first`
            : 'Posted double-entry transactions'
        }
        actions={
          <Button
            variant="primary"
            icon={<Plus className="h-3.5 w-3.5" strokeWidth={2.4} />}
            onClick={() => navigate('/transactions/new')}
          >
            New transaction
          </Button>
        }
      />

      <div className="px-6 py-5">
        <Panel>
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
            <div className="relative min-w-56 flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-3" />
              <Input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  reset();
                }}
                placeholder="Search reference, description or external reference"
                className="pl-8"
              />
            </div>
            <Select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                reset();
              }}
              className="w-36"
            >
              <option value="">Any status</option>
              <option value="POSTED">Posted</option>
              <option value="REVERSED">Reversed</option>
            </Select>
            <Select
              value={kind}
              onChange={(event) => {
                setKind(event.target.value);
                reset();
              }}
              className="w-36"
            >
              <option value="">Any kind</option>
              <option value="STANDARD">Standard</option>
              <option value="REVERSAL">Reversal</option>
            </Select>
            <Select
              value={currency}
              onChange={(event) => {
                setCurrency(event.target.value);
                reset();
              }}
              className="w-32"
            >
              <option value="">Currency</option>
              {(currencies ?? []).map((option) => (
                <option key={option.code} value={option.code}>
                  {option.code}
                </option>
              ))}
            </Select>
            {(search || status || kind || currency) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch('');
                  setStatus('');
                  setKind('');
                  setCurrency('');
                  reset();
                }}
              >
                Clear
              </Button>
            )}
          </div>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} />
          ) : query.isLoading ? (
            <TableSkeleton rows={10} columns={6} />
          ) : query.data && query.data.items.length === 0 ? (
            <EmptyState
              title="No transactions match these filters"
              description="Every posting is recorded permanently, so this view never hides anything - only filters it."
              action={
                <Button variant="primary" size="sm" onClick={() => navigate('/transactions/new')}>
                  Post a transaction
                </Button>
              }
            />
          ) : (
            <>
              <TableScroller>
                <Table>
                  <THead>
                    <th className="th">Reference</th>
                    <th className="th">Description</th>
                    <th className="th">Accounts</th>
                    <th className="th">Posted</th>
                    <th className="th text-right">Legs</th>
                    <th className="th text-right">Amount</th>
                    <th className="th">Status</th>
                  </THead>
                  <tbody>
                    {query.data?.items.map((transaction) => {
                      const codes = [
                        ...new Set(
                          transaction.entries
                            .map((entry) => entry.account?.code)
                            .filter((code): code is string => Boolean(code)),
                        ),
                      ];
                      return (
                        <tr
                          key={transaction.id}
                          className="row-link"
                          onClick={() => navigate(`/transactions/${transaction.id}`)}
                        >
                          <td className="td num font-medium whitespace-nowrap">
                            {transaction.reference}
                          </td>
                          <td className="td max-w-[20rem] truncate text-ink-2">
                            {transaction.description}
                            {transaction.external_reference && (
                              <span className="num ml-2 text-2xs text-ink-3">
                                {transaction.external_reference}
                              </span>
                            )}
                          </td>
                          <td className="td">
                            <span className="num text-xs text-ink-3">
                              {codes.slice(0, 2).join(' → ')}
                              {codes.length > 2 && ` +${codes.length - 2}`}
                            </span>
                          </td>
                          <td className="td whitespace-nowrap text-ink-3">
                            {formatDateTime(transaction.posted_at)}
                          </td>
                          <td className="td num text-right text-ink-3">
                            {transaction.entry_count}
                          </td>
                          <td className="td text-right">
                            <Amount money={transaction.total_debits} showCurrency />
                          </td>
                          <td className="td">
                            <TransactionStatusBadge
                              status={transaction.status}
                              kind={transaction.kind}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </Table>
              </TableScroller>
              <Pagination
                total={query.data?.total ?? 0}
                limit={LIMIT}
                offset={offset}
                onChange={setOffset}
                unit="transactions"
              />
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
