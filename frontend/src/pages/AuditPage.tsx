/** Audit log: intent behind every write, including attempts that changed state. */

import { useState } from 'react';
import { Link } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { CopyableId } from '../components/ledger/atoms';
import { Badge, Button, Panel, Select } from '../components/ui/primitives';
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
import { useAuditEvents } from '../lib/queries';

const LIMIT = 50;

const EVENT_TYPES = [
  'transaction.posted',
  'transaction.reversed',
  'account.created',
  'account.updated',
];

export default function AuditPage() {
  const [eventType, setEventType] = useState('');
  const [resourceType, setResourceType] = useState('');
  const [offset, setOffset] = useState(0);

  const query = useAuditEvents({
    event_type: eventType || undefined,
    resource_type: resourceType || undefined,
    limit: LIMIT,
    offset,
  });

  return (
    <>
      <PageHeader
        title="Audit log"
        description={
          query.data
            ? `${formatInteger(query.data.total)} events, append-only`
            : 'Who changed what, under which request'
        }
      />

      <div className="px-5 py-6 sm:px-8">
        <Panel>
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
            <Select
              value={eventType}
              onChange={(event) => {
                setEventType(event.target.value);
                setOffset(0);
              }}
              className="w-52"
            >
              <option value="">All event types</option>
              {EVENT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </Select>
            <Select
              value={resourceType}
              onChange={(event) => {
                setResourceType(event.target.value);
                setOffset(0);
              }}
              className="w-40"
            >
              <option value="">All resources</option>
              <option value="transaction">Transaction</option>
              <option value="account">Account</option>
            </Select>
            {(eventType || resourceType) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setEventType('');
                  setResourceType('');
                  setOffset(0);
                }}
              >
                Clear
              </Button>
            )}
          </div>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} />
          ) : query.isLoading ? (
            <TableSkeleton rows={10} columns={5} />
          ) : query.data && query.data.items.length === 0 ? (
            <EmptyState title="No audit events recorded yet" />
          ) : (
            <>
              <TableScroller>
                <Table>
                  <THead>
                    <th className="th">Seq</th>
                    <th className="th">Recorded</th>
                    <th className="th">Event</th>
                    <th className="th">Resource</th>
                    <th className="th">Actor</th>
                    <th className="th">Request</th>
                    <th className="th">Payload</th>
                  </THead>
                  <tbody>
                    {query.data?.items.map((event) => (
                      <tr key={event.id}>
                        <td className="td num text-ink-3">{event.seq}</td>
                        <td className="td whitespace-nowrap text-ink-3">
                          {formatDateTime(event.created_at)}
                        </td>
                        <td className="td">
                          <Badge
                            tone={event.event_type.endsWith('reversed') ? 'warning' : 'accent'}
                            mono
                          >
                            {event.event_type}
                          </Badge>
                        </td>
                        <td className="td">
                          {event.resource_id ? (
                            <Link
                              to={
                                event.resource_type === 'transaction'
                                  ? `/app/transactions/${event.resource_id}`
                                  : `/app/accounts/${event.resource_id}`
                              }
                              className="num text-[0.8125rem] text-accent-ink hover:underline"
                            >
                              {event.resource_type}/{event.resource_id.slice(0, 8)}…
                            </Link>
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                        <td className="td num text-ink-2">{event.actor}</td>
                        <td className="td">
                          {event.request_id ? (
                            <CopyableId value={event.request_id} truncate={10} />
                          ) : (
                            <span className="text-ink-3">—</span>
                          )}
                        </td>
                        <td className="td max-w-[24rem]">
                          <code className="num block truncate text-2xs text-ink-3">
                            {JSON.stringify(event.payload)}
                          </code>
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
                unit="events"
              />
            </>
          )}
        </Panel>
      </div>
    </>
  );
}
