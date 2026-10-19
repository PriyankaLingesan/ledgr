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
import { CaseStatusBadge, PriorityBadge } from '../components/ops/atoms';
import { Modal } from '../components/ui/Modal';
import { Button, Field, Input, Panel, Select, Textarea } from '../components/ui/primitives';
import {
  EmptyState,
  ErrorState,
  Pagination,
  Table,
  TableScroller,
  TableSkeleton,
  THead,
} from '../components/ui/Table';
import { useAuth } from '../lib/auth';
import { useDebounced } from '../lib/hooks';
import { formatDateTime } from '../lib/format';
import { useCreateInvestigation, useInvestigations } from '../lib/queries';
import { useToast } from '../lib/toast';
import type { InvestigationPriority } from '../lib/types';

const LIMIT = 25;

function NewCaseDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [title, setTitle] = useState('');
  const [issueSummary, setIssueSummary] = useState('');
  const [priority, setPriority] = useState<InvestigationPriority>('MEDIUM');
  const navigate = useNavigate();
  const toast = useToast();
  const create = useCreateInvestigation();

  const submit = () => {
    if (!title.trim() || !issueSummary.trim()) return;
    create.mutate(
      { title: title.trim(), issue_summary: issueSummary.trim(), source: 'MANUAL', priority },
      {
        onSuccess: (created) => {
          onClose();
          setTitle('');
          setIssueSummary('');
          setPriority('MEDIUM');
          toast.success(`Opened ${created.case_number}`);
          navigate(`/app/investigations/${created.id}`);
        },
        onError: (error) => toast.apiError(error),
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
        <Field label="Priority" htmlFor="case-priority">
          <Select
            id="case-priority"
            value={priority}
            onChange={(event) => setPriority(event.target.value as InvestigationPriority)}
          >
            <option value="LOW">Low</option>
            <option value="MEDIUM">Medium</option>
            <option value="HIGH">High</option>
            <option value="CRITICAL">Critical</option>
          </Select>
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
  const { can } = useAuth();
  const canWrite = can('investigations:write');
  const [status, setStatus] = useState(searchParams.get('status') ?? '');
  const [priority, setPriority] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [newCaseOpen, setNewCaseOpen] = useState(false);
  const debouncedSearch = useDebounced(search);

  const query = useInvestigations({
    status: status || undefined,
    priority: priority || undefined,
    q: debouncedSearch.trim() || undefined,
    limit: LIMIT,
    offset,
  });
  const filtered = Boolean(status || priority || search);

  return (
    <>
      <PageHeader
        title="Investigations"
        description="Open, in-progress and resolved cases - the workflow behind every control exception and discrepancy."
        actions={
          canWrite && (
            <Button
              variant="primary"
              icon={<Plus className="h-3.5 w-3.5" strokeWidth={2.4} />}
              onClick={() => setNewCaseOpen(true)}
            >
              New investigation
            </Button>
          )
        }
      />

      <div className="px-5 py-6 sm:px-8">
        <Panel>
          <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2.5">
            <Input
              aria-label="Search investigations"
              className="w-56"
              placeholder="Case number or title"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setOffset(0);
              }}
            />
            <Select
              aria-label="Filter by priority"
              value={priority}
              onChange={(event) => {
                setPriority(event.target.value);
                setOffset(0);
              }}
              className="w-36"
            >
              <option value="">Any priority</option>
              <option value="CRITICAL">Critical</option>
              <option value="HIGH">High</option>
              <option value="MEDIUM">Medium</option>
              <option value="LOW">Low</option>
            </Select>
            <Select
              aria-label="Filter by status"
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
              title={filtered ? 'No investigations match these filters' : 'No investigations yet'}
              description={
                filtered
                  ? 'Try clearing a filter.'
                  : 'Cases opened from reconciliation exceptions, control exceptions or the Flight Recorder will appear here.'
              }
              action={
                !filtered && canWrite ? (
                  <Button variant="primary" size="sm" onClick={() => setNewCaseOpen(true)}>
                    Open an investigation
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <>
              <TableScroller>
                <Table>
                  <THead>
                    <th className="th">Case</th>
                    <th className="th">Title</th>
                    <th className="th">Priority</th>
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
                        <td className="td">
                          <PriorityBadge priority={investigationCase.priority} />
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
                          <CaseStatusBadge status={investigationCase.status} />
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
