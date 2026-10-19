/**
 * Account statement: entries with a running balance.
 *
 * The running balance is computed by the database over the account's entire
 * history and only then filtered, so searching, filtering or paging never
 * changes a row's balance. Everything is derived from ledger entries; nothing
 * is stored or editable.
 */

import { Download } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { useDebounced } from '../../lib/hooks';
import { formatDateTime, formatMoney } from '../../lib/format';
import { useCurrencies } from '../../lib/queries';
import { useStatement } from '../../lib/opsQueries';
import type { StatementRow } from '../../lib/opsTypes';
import { Badge, Button, Input, Select, Skeleton } from '../ui/primitives';
import { EmptyState, ErrorState, Pagination, Table, TableScroller, TableSkeleton, THead } from '../ui/Table';
import { Amount } from './atoms';

const LIMIT = 25;

function dayBound(value: string, end: boolean): string | undefined {
  if (!value) return undefined;
  const date = new Date(`${value}T${end ? '23:59:59.999' : '00:00:00'}`);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function csvCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

export function StatementPanel({ accountId }: { accountId: string }) {
  const { data: currencies } = useCurrencies();
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [direction, setDirection] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const debouncedSearch = useDebounced(search);

  const query = useStatement(accountId, {
    since: dayBound(from, false),
    until: dayBound(to, true),
    direction: direction || undefined,
    q: debouncedSearch.trim() || undefined,
    limit: LIMIT,
    offset,
  });
  const data = query.data;
  const filtered = Boolean(from || to || direction || search);

  const exportPage = (rows: StatementRow[]) => {
    if (!data) return;
    const header = ['Posted', 'Reference', 'Description', 'Debit', 'Credit', 'Running balance'];
    const lines = rows.map((r) =>
      [
        r.posted_at,
        r.reference,
        r.description,
        r.debit ? formatMoney(r.debit, { registry: currencies, showCurrency: false }) : '',
        r.credit ? formatMoney(r.credit, { registry: currencies, showCurrency: false }) : '',
        formatMoney(r.running_balance, { registry: currencies, showCurrency: false }),
      ]
        .map(csvCell)
        .join(','),
    );
    const blob = new Blob([[header.join(','), ...lines].join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `statement-${data.account.code}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      {/* period summary: opening + debits - credits = closing (in the account's normal-balance sense) */}
      <div className="grid grid-cols-2 gap-px border-b border-border bg-border lg:grid-cols-4">
        {[
          ['Opening balance', data?.opening_balance],
          ['Total debits', data?.total_debits],
          ['Total credits', data?.total_credits],
          ['Closing balance', data?.closing_balance],
        ].map(([label, money]) => (
          <div key={label as string} className="bg-surface px-4 py-3">
            <p className="caption">{label as string}</p>
            {data ? (
              <p className="mt-1">
                <Amount money={money as typeof data.opening_balance} showCurrency />
              </p>
            ) : (
              <Skeleton className="mt-2 h-5 w-24" />
            )}
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-end gap-2 border-b border-border px-3 py-2.5">
        <label className="flex flex-col gap-1 text-xs text-ink-3">
          From
          <Input
            type="date"
            className="w-40"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              setOffset(0);
            }}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-3">
          To
          <Input
            type="date"
            className="w-40"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              setOffset(0);
            }}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-ink-3">
          Side
          <Select
            className="w-32"
            value={direction}
            onChange={(e) => {
              setDirection(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">Debit & credit</option>
            <option value="DEBIT">Debits</option>
            <option value="CREDIT">Credits</option>
          </Select>
        </label>
        <label className="flex min-w-48 flex-1 flex-col gap-1 text-xs text-ink-3 sm:max-w-xs">
          Search
          <Input
            placeholder="Reference, description or memo"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setOffset(0);
            }}
          />
        </label>
        {filtered && (
          <Button
            variant="ghost"
            onClick={() => {
              setFrom('');
              setTo('');
              setDirection('');
              setSearch('');
              setOffset(0);
            }}
          >
            Clear
          </Button>
        )}
        <Button
          className="ml-auto"
          icon={<Download className="h-3.5 w-3.5" />}
          disabled={!data || data.items.length === 0}
          onClick={() => data && exportPage(data.items)}
          title="Downloads the rows currently shown"
        >
          Export page (CSV)
        </Button>
      </div>

      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} context="the statement" />
      ) : query.isLoading ? (
        <TableSkeleton rows={8} columns={6} />
      ) : data && data.items.length === 0 ? (
        <EmptyState
          title={filtered ? 'No entries match these filters' : 'No entries yet'}
          description={filtered ? 'Try widening the date range.' : 'This account has never been posted to.'}
        />
      ) : (
        data && (
          <>
            <TableScroller>
              <Table>
                <THead>
                  <th className="px-3 py-2 text-xs font-medium text-ink-3">Posted</th>
                  <th className="px-3 py-2 text-xs font-medium text-ink-3">Transaction</th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-ink-3">Debit</th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-ink-3">Credit</th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-ink-3">Balance</th>
                </THead>
                <tbody className="divide-y divide-border">
                  {data.items.map((row) => (
                    <tr key={row.entry_id} className="align-top hover:bg-surface-2">
                      <td className="num whitespace-nowrap px-3 py-2.5 text-[0.8125rem] text-ink-2">
                        {formatDateTime(row.posted_at)}
                      </td>
                      <td className="px-3 py-2.5">
                        <Link
                          to={`/app/transactions/${row.transaction_id}`}
                          className="num text-[0.8125rem] font-medium text-ink hover:underline"
                        >
                          {row.reference}
                        </Link>
                        {row.transaction_kind === 'REVERSAL' && (
                          <Badge tone="neutral" className="ml-2">
                            Reversal
                          </Badge>
                        )}
                        {row.transaction_status === 'REVERSED' && (
                          <Badge tone="warning" className="ml-2">
                            Reversed
                          </Badge>
                        )}
                        <p className="max-w-md truncate text-xs text-ink-3">
                          {row.description}
                          {row.memo ? ` — ${row.memo}` : ''}
                        </p>
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {row.debit ? <Amount money={row.debit} tone="debit" /> : <span className="text-ink-3">—</span>}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {row.credit ? <Amount money={row.credit} tone="credit" /> : <span className="text-ink-3">—</span>}
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        <Amount money={row.running_balance} showCurrency tone="auto" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </TableScroller>
            <Pagination total={data.total} limit={LIMIT} offset={offset} onChange={setOffset} unit="entries" />
            <p className="border-t border-border px-4 py-2.5 text-2xs text-ink-3">
              Balances are in this account's normal-balance sense ({data.account.normal_balance.toLowerCase()}-normal),
              computed from ledger entries - the ledger is the only source of truth.
            </p>
          </>
        )
      )}
    </div>
  );
}
