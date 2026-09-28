/**
 * Investigation case register.
 *
 * Every case here is the operational record of "someone looked into this" -
 * a status, a triggering signal, sometimes an account. No case carries money
 * of its own; every figure behind a case lives on its own detail page,
 * computed live from the ledger.
 */

import { Loader2, Plus, SearchCheck } from 'lucide-react';
import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { PageHeader } from '../components/layout/AppShell';
import { Modal } from '../components/ui/Modal';
import { Badge, Button, Field, Input, Panel, Select, Textarea } from '../components/ui/primitives';
import {
  EmptyState,
  ErrorState,
  Pagination,
  Table,
  TableScroller,
  TableSkeleton,
  THead,
} from '../components/ui/Table';
import { formatDateTime } from '../lib/format';
import { useCreateInvestigation, useInvestigations } from '../lib/queries';
import type { InvestigationStatus } from '../lib/types';

const LIMIT = 25;

function StatusBadge({ status }: { status: InvestigationStatus }) {
  const tone = status === 'OPEN' ? 'warning' : status === 'INVESTIGATING' ? 'accent' : 'positive';
  return <Badge tone={tone}>{status}</Badge>;
}

function NewCaseDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [title, setTitle] = useState('');
  const [issueSummary, setIssueSummary] = useState('');
  const navigate = useNavigate();
  const create = useCreateInvestigation();

  const submit = () => {
    if (!title.trim() || !issueSummary.trim()) return;
    create.mutate(
      { title: title.trim(), issue_summary: issueSummary.trim(), source: 'MANUAL' },
      {
        onSuccess: (created) => {
          onClose();
          setTitle('');
          setIssueSummary('');
          navigate(`/app/investigations/${created.id}`);
        },
      },
    );
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Open an investigation"
      description="A manual case - for a discrepancy or question that didn't come from an automated control."
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={create.isPending || !title.trim() || !issueSummary.trim()}
            icon={create.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : undefined}
            onClick={submit}
          >
            Open case
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Title" htmlFor="case-title" required>
          <Input
            id="case-title"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Cash balance looks too high"
          />
        </Field>
        <Field label="What's the issue?" htmlFor="case-summary" required>
          <Textarea
            id="case-summary"
            value={issueSummary}
            onChange={(event) => setIssueSummary(event.target.value)}
            placeholder="Describe what looked wrong and where you noticed it"
          />
        </Field>
      </div>
    </Modal>
  );
}

export default function InvestigationsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [status, setStatus] = useState(searchParams.get('status') ?? '');
  const [offset, setOffset] = useState(0);
  const [newCaseOpen, setNewCaseOpen] = useState(false);

  const query = useInvestigations({ status: status || undefined, limit: LIMIT, offset });

  return (
    <>
      <PageHeader
        title="Investigations"
        description="Open, in-progress and resolved cases - the workflow behind every control exception and discrepancy."
        actions={
          <Button
            variant="primary"
            icon={<Plus className="h-3.5 w-3.5" strokeWidth={2.4} />}
            onClick={() => setNewCaseOpen(true)}
          >
            New investigation
          </Button>
        }
      />

      <div className="px-5 py-6 sm:px-8">
        <Panel>
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
            <Select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
              className="w-40"
            >
              <option value="">Any status</option>
              <option value="OPEN">Open</option>
              <option value="INVESTIGATING">Investigating</option>
              <option value="RESOLVED">Resolved</option>
            </Select>
          </div>

          {query.isError ? (
            <ErrorState error={query.error} onRetry={() => query.refetch()} />
          ) : query.isLoading ? (
            <TableSkeleton rows={8} columns={5} />
          ) : query.data && query.data.items.length === 0 ? (
            <EmptyState
              icon={<SearchCheck className="h-4 w-4" strokeWidth={1.6} />}
              title="No investigations yet"
              description="Cases opened from control exceptions or the Flight Recorder will appear here, or start one manually."
              action={
                <Button variant="primary" size="sm" onClick={() => setNewCaseOpen(true)}>
                  Open an investigation
                </Button>
              }
            />
          ) : (
            <>
              <TableScroller>
                <Table>
                  <THead>
                    <th className="th">Case</th>
                    <th className="th">Title</th>
                    <th className="th">Account</th>
                    <th className="th">Source</th>
                    <th className="th">Opened</th>
                    <th className="th">Status</th>
                  </THead>
                  <tbody>
                    {query.data?.items.map((investigationCase) => (
                      <tr
                        key={investigationCase.id}
                        className="row-link"
                        onClick={() => navigate(`/app/investigations/${investigationCase.id}`)}
                      >
                        <td className="td num font-medium whitespace-nowrap">
                          {investigationCase.case_number}
                        </td>
                        <td className="td max-w-[22rem] truncate text-ink-2">
                          {investigationCase.title}
                        </td>
                        <td className="td num text-xs text-ink-3">
                          {investigationCase.account?.code ?? '—'}
                        </td>
                        <td className="td">
                          <span className="text-xs text-ink-3">{investigationCase.source}</span>
                        </td>
                        <td className="td whitespace-nowrap text-ink-3">
                          {formatDateTime(investigationCase.created_at)}
                        </td>
                        <td className="td">
                          <StatusBadge status={investigationCase.status} />
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
                unit="cases"
              />
            </>
          )}
        </Panel>
      </div>

      <NewCaseDialog open={newCaseOpen} onClose={() => setNewCaseOpen(false)} />
    </>
  );
}
