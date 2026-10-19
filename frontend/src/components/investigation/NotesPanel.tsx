/**
 * Investigator notes. Append-only: a note cannot be edited or deleted (the server
 * and the database both refuse), because what was concluded, by whom and when is
 * itself evidence.
 */

import { MessageSquarePlus } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { useAuth } from '../../lib/auth';
import { formatDateTime } from '../../lib/format';
import { useAddCaseNote, useCaseNotes } from '../../lib/opsQueries';
import { useToast } from '../../lib/toast';
import { Button, Panel, PanelHeader, Skeleton, Textarea } from '../ui/primitives';
import { EmptyState, ErrorState } from '../ui/Table';

export function NotesPanel({ caseId }: { caseId: string }) {
  const { can } = useAuth();
  const toast = useToast();
  const notes = useCaseNotes(caseId);
  const add = useAddCaseNote();
  const [body, setBody] = useState('');

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!body.trim()) return;
    add.mutate(
      { caseId, body: body.trim() },
      {
        onSuccess: () => {
          setBody('');
          toast.success('Note added');
        },
        onError: (error) => toast.apiError(error),
      },
    );
  };

  return (
    <Panel>
      <PanelHeader
        title="Investigator notes"
        description="Append-only - notes cannot be edited or deleted"
      />
      {notes.isError ? (
        <ErrorState error={notes.error} onRetry={() => notes.refetch()} context="notes" />
      ) : notes.isLoading ? (
        <div className="p-4">
          <Skeleton className="h-16 w-full" />
        </div>
      ) : notes.data && notes.data.length > 0 ? (
        <ul className="divide-y divide-border">
          {notes.data.map((note) => (
            <li key={note.id} className="px-4 py-3">
              <p className="whitespace-pre-wrap break-words text-[0.875rem] text-ink">{note.body}</p>
              <p className="mt-1 text-xs text-ink-3">
                {note.author} · {formatDateTime(note.created_at)}
              </p>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          icon={<MessageSquarePlus className="h-4 w-4" strokeWidth={1.6} />}
          title="No notes yet"
          description="Record what you checked, who you spoke to and what you concluded."
        />
      )}

      {can('investigations:write') && (
        <form onSubmit={submit} className="flex flex-col gap-2 border-t border-border px-4 py-3">
          <label htmlFor="case-note" className="field-label">
            Add a note
          </label>
          <Textarea
            id="case-note"
            value={body}
            maxLength={4000}
            onChange={(event) => setBody(event.target.value)}
            placeholder="e.g. Called the provider; they confirmed the payout was net of a 5.00 fee."
          />
          <div className="flex justify-end">
            <Button type="submit" variant="primary" size="sm" disabled={add.isPending || !body.trim()}>
              Add note
            </Button>
          </div>
        </form>
      )}
    </Panel>
  );
}
