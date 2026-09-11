/** Chart of accounts: filterable, dense, balance-first. */

import { Plus, Search } from 'lucide-react';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import {
  AccountStatusBadge,
  AccountTypeBadge,
  Amount,
} from '../components/ledger/atoms';
import { Modal } from '../components/ui/Modal';
import {
  Button,
  Field,
  Input,
  Panel,
  Select,
  Spinner,
} from '../components/ui/primitives';
import {
  EmptyState,
  ErrorState,
  Pagination,
  Table,
  TableScroller,
  TableSkeleton,
  THead,
} from '../components/ui/Table';
import { ApiError } from '../lib/api';
import { formatInteger } from '../lib/format';
import { useDebounced } from '../lib/hooks';
import { useAccounts, useCreateAccount, useCurrencies } from '../lib/queries';
import type { AccountType } from '../lib/types';

const ACCOUNT_TYPES: AccountType[] = ['ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE'];
const LIMIT = 25;

function NewAccountDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data: currencies } = useCurrencies();
  const createAccount = useCreateAccount();
  const [form, setForm] = useState({
    code: '',
    name: '',
    type: 'ASSET' as AccountType,
    currency: 'USD',
    description: '',
    allows_negative_balance: true,
  });

  const error = createAccount.error instanceof ApiError ? createAccount.error : null;

  const submit = () => {
    createAccount.mutate(
      {
        code: form.code.trim(),
        name: form.name.trim(),
        type: form.type,
        currency: form.currency,
        description: form.description.trim() || null,
        allows_negative_balance: form.allows_negative_balance,
      },
      {
        onSuccess: () => {
          setForm({
            code: '',
            name: '',
            type: 'ASSET',
            currency: 'USD',
            description: '',
            allows_negative_balance: true,
          });
          onClose();
        },
      },
    );
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New account"
      description="An account classifies entries; its balance is always derived from them."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!form.code.trim() || !form.name.trim() || createAccount.isPending}
            onClick={submit}
            icon={createAccount.isPending ? <Spinner /> : undefined}
          >
            Create account
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-3">
        <Field label="Account code" required htmlFor="code" hint="Stable handle, e.g. CASH.OPERATING.USD">
          <Input
            id="code"
            value={form.code}
            onChange={(event) => setForm({ ...form, code: event.target.value.toUpperCase() })}
            placeholder="CASH.OPERATING.USD"
            className="num"
          />
        </Field>
        <Field label="Display name" required htmlFor="name">
          <Input
            id="name"
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            placeholder="Operating Bank Account"
          />
        </Field>
        <Field label="Type" htmlFor="type" hint="Determines the normal balance side">
          <Select
            id="type"
            value={form.type}
            onChange={(event) => setForm({ ...form, type: event.target.value as AccountType })}
          >
            {ACCOUNT_TYPES.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Currency" htmlFor="currency" hint="Cannot change once entries exist">
          <Select
            id="currency"
            value={form.currency}
            onChange={(event) => setForm({ ...form, currency: event.target.value })}
          >
            {(currencies ?? []).map((currency) => (
              <option key={currency.code} value={currency.code}>
                {currency.code} — {currency.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Description" htmlFor="description" className="col-span-2">
          <Input
            id="description"
            value={form.description}
            onChange={(event) => setForm({ ...form, description: event.target.value })}
            placeholder="Optional"
          />
        </Field>
        <label className="col-span-2 flex cursor-pointer items-start gap-2.5 rounded-sm border border-border bg-surface-2 px-3 py-2.5">
          <input
            type="checkbox"
            className="mt-0.5 h-3.5 w-3.5 accent-[var(--accent)]"
            checked={!form.allows_negative_balance}
            onChange={(event) =>
              setForm({ ...form, allows_negative_balance: !event.target.checked })
            }
          />
          <span className="text-xs leading-relaxed">
            <span className="font-medium text-ink">Enforce a non-negative balance</span>
            <span className="block text-ink-3">
              Postings that would drive this account below zero are rejected. The check runs under a
              row lock, so concurrent withdrawals cannot both succeed.
            </span>
          </span>
        </label>
      </div>

      {error && (
        <p className="mt-3 rounded-sm border border-negative/30 bg-negative-soft px-3 py-2 text-xs text-negative">
          <span className="num font-semibold">{error.code}</span> — {error.message}
        </p>
      )}
    </Modal>
  );
}

export default function AccountsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [type, setType] = useState('');
  const [status, setStatus] = useState('');
  const [currency, setCurrency] = useState('');
  const [offset, setOffset] = useState(0);
  const [dialogOpen, setDialogOpen] = useState(false);

  const debouncedSearch = useDebounced(search);
  const { data: currencies } = useCurrencies();

  const query = useAccounts({
    q: debouncedSearch || undefined,
    type: type || undefined,
    status: status || undefined,
    currency: currency || undefined,
    limit: LIMIT,
    offset,
  });

  const resetOffset = () => setOffset(0);

  return (
    <>
      <PageHeader
        title="Accounts"
        description={
          query.data
            ? `${formatInteger(query.data.total)} accounts in the chart of accounts`
            : 'Chart of accounts'
        }
        actions={
          <Button
            variant="primary"
            icon={<Plus className="h-3.5 w-3.5" strokeWidth={2.4} />}
            onClick={() => setDialogOpen(true)}
          >
            New account
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
                  resetOffset();
                }}
                placeholder="Search by code or name"
                className="pl-8"
              />
            </div>
            <Select
              value={type}
              onChange={(event) => {
                setType(event.target.value);
                resetOffset();
              }}
              className="w-40"
            >
              <option value="">All types</option>
              {ACCOUNT_TYPES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </Select>
            <Select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                resetOffset();
              }}
              className="w-36"
            >
              <option value="">Any status</option>
              <option value="ACTIVE">Active</option>
              <option value="FROZEN">Frozen</option>
              <option value="CLOSED">Closed</option>
            </Select>
            <Select
              value={currency}
              onChange={(event) => {
                setCurrency(event.target.value);
                resetOffset();
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
            {(search || type || status || currency) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch('');
                  setType('');
                  setStatus('');
                  setCurrency('');
                  resetOffset();
                }}
              >
                Clear
              </Button>
            )}
          </div>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} />
          ) : query.isLoading ? (
            <TableSkeleton rows={8} columns={6} />
          ) : query.data && query.data.items.length === 0 ? (
            <EmptyState
              title="No accounts match these filters"
              description="Adjust the filters, or create the first account in this chart."
              action={
                <Button variant="primary" size="sm" onClick={() => setDialogOpen(true)}>
                  New account
                </Button>
              }
            />
          ) : (
            <>
              <TableScroller>
                <Table>
                  <THead>
                    <th className="th">Code</th>
                    <th className="th">Name</th>
                    <th className="th">Type</th>
                    <th className="th">Normal</th>
                    <th className="th">Currency</th>
                    <th className="th text-right">Entries</th>
                    <th className="th text-right">Balance</th>
                    <th className="th">Status</th>
                  </THead>
                  <tbody>
                    {query.data?.items.map((account) => (
                      <tr
                        key={account.id}
                        className="row-link"
                        onClick={() => navigate(`/accounts/${account.id}`)}
                      >
                        <td className="td num font-medium">{account.code}</td>
                        <td className="td max-w-[20rem] truncate text-ink-2">
                          {account.name}
                          {!account.allows_negative_balance && (
                            <span className="ml-2 text-2xs text-ink-3">· balance floor</span>
                          )}
                        </td>
                        <td className="td">
                          <AccountTypeBadge type={account.type} />
                        </td>
                        <td className="td">
                          <span
                            className={
                              account.normal_balance === 'DEBIT'
                                ? 'text-2xs font-semibold uppercase tracking-wide text-debit-ink'
                                : 'text-2xs font-semibold uppercase tracking-wide text-credit-ink'
                            }
                          >
                            {account.normal_balance === 'DEBIT' ? 'DR' : 'CR'}
                          </span>
                        </td>
                        <td className="td num text-ink-2">{account.currency}</td>
                        <td className="td num text-right text-ink-3">
                          {formatInteger(account.entry_count)}
                        </td>
                        <td className="td text-right">
                          <Amount money={account.balance} tone="auto" />
                        </td>
                        <td className="td">
                          <AccountStatusBadge status={account.status} />
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
                unit="accounts"
              />
            </>
          )}
        </Panel>

        <p className="mt-3 text-xs text-ink-3">
          Balances shown here are read from the transactionally-maintained cache. Open an account to
          see the balance re-derived from its ledger entries.
        </p>
      </div>

      <NewAccountDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
    </>
  );
}
