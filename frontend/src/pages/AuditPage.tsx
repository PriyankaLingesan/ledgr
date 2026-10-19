/**
 * Audit log: every tracked action, with who did it, to what, when and under
 * which request. Read-only - there is no API to create, edit or delete an
 * event, and the database rejects UPDATE/DELETE on the table. Nothing here is
 * synthesised: each row is a real `audit_events` record.
 */

import { ChevronDown, ChevronRight } from 'lucide-react';
import { Fragment, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { CopyableId } from '../components/ledger/atoms';
import { AuditActionBadge, auditLabel } from '../components/ops/atoms';
import { Button, Field, Input, Panel, Select } from '../components/ui/primitives';
import {
  EmptyState,
  ErrorState,
  Pagination,
  Table,
  TableScroller,
  TableSkeleton,
  THead,
} from '../components/ui/Table';
import { useDebounced } from '../lib/hooks';
import { formatDateTime, formatInteger } from '../lib/format';
import { useAuditEventTypes } from '../lib/opsQueries';
import { useAuditEvents } from '../lib/queries';

const LIMIT = 50;

/** `<input type="date">` value -> ISO bound (start/end of that local day). */
function dayBound(value: string, end: boolean): string | undefined {
  if (!value) return undefined;
  const date = new Date(`${value}T${end ? '23:59:59.999' : '00:00:00'}`);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export default function AuditPage() {
  const [params, setParams] = useSearchParams();
  const [eventType, setEventType] = useState(params.get('event_type') ?? '');
  const [actor, setActor] = useState('');
  const [search, setSearch] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  const debouncedSearch = useDebounced(search);
  const debouncedActor = useDebounced(actor);
  const types = useAuditEventTypes();

  // Keep the URL in step with the type filter so alert links ("/audit?event_type=...") are shareable.
  useEffect(() => {
    const next = new URLSearchParams(params);
    if (eventType) next.set('event_type', eventType);
    else next.delete('event_type');
    if (next.toString() !== params.toString()) setParams(next, { replace: true });
  }, [eventType, params, setParams]);

  const query = useAuditEvents({
    event_type: eventType || undefined,
    actor: debouncedActor.trim() || undefined,
    q: debouncedSearch.trim() || undefined,
    created_from: dayBound(from, false),
    created_to: dayBound(to, true),
    limit: LIMIT,
    offset,
  });

  const filtered = Boolean(eventType || actor || search || from || to);
  const reset = () => {
    setEventType('');
    setActor('');
    setSearch('');
    setFrom('');
    setTo('');
    setOffset(0);
  };

  return (
    <>
      <PageHeader
        title="Audit log"
        description={
          query.data
            ? `${formatInteger(query.data.total)} event${query.data.total === 1 ? '' : 's'}${filtered ? ' match' : ''} - append-only, cannot be edited`
            : 'Who did what, to which record, under which request'
        }
      />

      <div className="px-5 py-6 sm:px-8">
        <Panel>
          <div className="grid gap-3 border-b border-border px-3 py-3 sm:grid-cols-2 lg:grid-cols-5">
            <Field label="Search" htmlFor="audit-search">
              <Input
                id="audit-search"
                value={search}
                placeholder="Action, user, resource or request id"
                onChange={(event) => {
                  setSearch(event.target.value);
                  setOffset(0);
                }}
              />
            </Field>
            <Field label="Action" htmlFor="audit-type">
              <Select
                id="audit-type"
                value={eventType}
                onChange={(event) => {
                  setEventType(event.target.value);
                  setOffset(0);
                }}
              >
                <option value="">All actions</option>
                {(types.data ?? (eventType ? [eventType] : [])).map((type) => (
                  <option key={type} value={type}>
                    {auditLabel(type)}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="User / key" htmlFor="audit-actor">
              <Input
                id="audit-actor"
                value={actor}
                placeholder="Exact email or key label"
                onChange={(event) => {
                  setActor(event.target.value);
                  setOffset(0);
                }}
              />
            </Field>
            <Field label="From" htmlFor="audit-from">
              <Input
                id="audit-from"
                type="date"
                value={from}
                onChange={(event) => {
                  setFrom(event.target.value);
                  setOffset(0);
                }}
              />
            </Field>
            <Field label="To" htmlFor="audit-to">
              <div className="flex gap-2">
                <Input
                  id="audit-to"
                  type="date"
                  value={to}
                  onChange={(event) => {
                    setTo(event.target.value);
                    setOffset(0);
                  }}
                />
                {filtered && (
                  <Button variant="ghost" onClick={reset}>
                    Clear
                  </Button>
                )}
              </div>
            </Field>
          </div>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} context="the audit log" />
          ) : query.isLoading ? (
            <TableSkeleton rows={8} columns={5} />
          ) : query.data && query.data.items.length > 0 ? (
            <>
              <TableScroller>
                <Table>
                  <THead>
                    <th className="w-8 px-3 py-2" />
                    <th className="px-3 py-2 text-xs font-medium text-ink-3">Time</th>
                    <th className="px-3 py-2 text-xs font-medium text-ink-3">Action</th>
                    <th className="px-3 py-2 text-xs font-medium text-ink-3">User / key</th>
                    <th className="px-3 py-2 text-xs font-medium text-ink-3">Resource</th>
                    <th className="px-3 py-2 text-xs font-medium text-ink-3">Request</th>
                  </THead>
                  <tbody className="divide-y divide-border">
                    {query.data.items.map((event) => {
                      const open = expanded === event.id;
                      const hasPayload = Object.keys(event.payload).length > 0;
                      return (
                        <Fragment key={event.id}>
                          <tr className="align-top hover:bg-surface-2">
                            <td className="px-3 py-2">
                              {hasPayload && (
                                <button
                                  type="button"
                                  className="text-ink-3 hover:text-ink"
                                  aria-expanded={open}
                                  aria-label={open ? 'Hide details' : 'Show details'}
                                  onClick={() => setExpanded(open ? null : event.id)}
                                >
                                  {open ? (
                                    <ChevronDown className="h-4 w-4" />
                                  ) : (
                                    <ChevronRight className="h-4 w-4" />
                                  )}
                                </button>
                              )}
                            </td>
                            <td className="num whitespace-nowrap px-3 py-2 text-[0.8125rem] text-ink-2">
                              {formatDateTime(event.created_at)}
                            </td>
                            <td className="px-3 py-2">
                              <AuditActionBadge eventType={event.event_type} />
                              <p className="num mt-0.5 text-2xs text-ink-3">{event.event_type}</p>
                            </td>
                            <td className="px-3 py-2 text-[0.8125rem] text-ink">{event.actor}</td>
                            <td className="px-3 py-2 text-[0.8125rem]">
                              <span className="text-ink-2">{event.resource_type}</span>
                              {event.resource_id && (
                                <div>
                                  <CopyableId value={event.resource_id} />
                                </div>
                              )}
                            </td>
                            <td className="px-3 py-2">
                              {event.request_id ? <CopyableId value={event.request_id} /> : <span className="text-ink-3">—</span>}
                            </td>
                          </tr>
                          {open && (
                            <tr className="bg-surface-2">
                              <td />
                              <td colSpan={5} className="px-3 py-3">
                                <p className="caption mb-1.5">Recorded details</p>
                                <pre className="num max-h-64 overflow-auto rounded-sm border border-border bg-surface p-3 text-xs leading-relaxed text-ink-2">
                                  {JSON.stringify(event.payload, null, 2)}
                                </pre>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </Table>
              </TableScroller>
              <Pagination
                total={query.data.total}
                limit={LIMIT}
                offset={offset}
                onChange={setOffset}
                unit="events"
              />
            </>
          ) : (
            <EmptyState
              title={filtered ? 'No events match these filters' : 'No audit events yet'}
              description={
                filtered
                  ? 'Try widening the date range or clearing a filter.'
                  : 'Actions such as signing in, posting a transaction or running a reconciliation are recorded here.'
              }
              action={
                filtered ? (
                  <Button size="sm" onClick={reset}>
                    Clear filters
                  </Button>
                ) : undefined
              }
            />
          )}
        </Panel>
      </div>
    </>
  );
}
