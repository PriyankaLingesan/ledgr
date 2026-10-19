/**
 * API keys. Administrator-only (`api_keys:manage`).
 *
 * Keys authenticate through the same path as every other request and carry a
 * role. Only a SHA-256 hash is stored, so the plaintext key is shown exactly
 * once, in the creation response - it cannot be recovered afterwards, only
 * replaced by minting and rolling over to a new key.
 */

import { Check, Copy, KeyRound, TriangleAlert } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { PageHeader } from '../../components/layout/AppShell';
import { ROLE_DESCRIPTIONS, RoleBadge } from '../../components/ops/atoms';
import { ConfirmDialog } from '../../components/ui/ConfirmDialog';
import { Modal } from '../../components/ui/Modal';
import { Badge, Button, Field, Input, Panel, Select } from '../../components/ui/primitives';
import { EmptyState, ErrorState, Table, TableScroller, TableSkeleton, THead } from '../../components/ui/Table';
import { formatDateTime, formatRelative } from '../../lib/format';
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from '../../lib/opsQueries';
import { useAuth } from '../../lib/auth';
import { canManageRole, type ApiKeyCreated, type ApiKeyRecord, type UserRole } from '../../lib/opsTypes';
import { useToast } from '../../lib/toast';

// API keys never carry OWNER; an ADMIN can only mint keys below ADMIN (same rule as users).
const KEY_ROLES: UserRole[] = ['AUDITOR', 'OPERATIONS', 'FINANCE', 'ADMIN'];

function CreateKeyModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (key: ApiKeyCreated) => void;
}) {
  const toast = useToast();
  const create = useCreateApiKey();
  const { user: me } = useAuth();
  const roles = KEY_ROLES.filter((r) => canManageRole(me?.role, r));
  const [label, setLabel] = useState('');
  const [role, setRole] = useState<UserRole>('AUDITOR');

  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(
      { label: label.trim(), role },
      {
        onSuccess: (key) => {
          setLabel('');
          setRole('AUDITOR');
          onCreated(key);
        },
        onError: (error) => toast.apiError(error),
      },
    );
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Create an API key"
      description="Name it after the system that will use it. Grant the least privilege that works."
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label="Name" htmlFor="key-label" required hint="Shown as the actor on everything this key does.">
          <Input
            id="key-label"
            value={label}
            placeholder="e.g. settlement-reporting"
            onChange={(e) => setLabel(e.target.value)}
            autoFocus
          />
        </Field>
        <Field label="Role" htmlFor="key-role" hint={ROLE_DESCRIPTIONS[role]}>
          <Select id="key-role" value={role} onChange={(e) => setRole(e.target.value as UserRole)}>
            {roles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex justify-end gap-2 border-t border-border pt-4">
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={create.isPending || !label.trim()}>
            Create key
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function NewKeyModal({ created, onClose }: { created: ApiKeyCreated | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);

  const close = () => {
    setCopied(false);
    setAcknowledged(false);
    onClose();
  };

  const copy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.key);
      setCopied(true);
    } catch {
      /* clipboard unavailable: the key is selectable below */
    }
  };

  return (
    <Modal
      open={Boolean(created)}
      onClose={acknowledged ? close : () => undefined}
      title="Copy your new API key"
      description={created ? `${created.label} · ${created.role}` : undefined}
      footer={
        <Button variant="primary" onClick={close} disabled={!acknowledged}>
          Done
        </Button>
      }
    >
      <div
        role="alert"
        className="mb-3 flex items-start gap-2 rounded-sm border border-warning/30 bg-warning-soft px-3 py-2.5 text-[0.8125rem] text-warning"
      >
        <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.9} />
        This is the only time the key is shown. LEDGR stores just a hash of it, so it cannot be
        displayed again.
      </div>
      <div className="flex items-center gap-2">
        <code
          className="num min-w-0 flex-1 select-all break-all rounded-sm border border-border bg-surface-2 px-2.5 py-2 text-[0.8125rem] text-ink"
          data-testid="new-api-key"
        >
          {created?.key}
        </code>
        <Button onClick={copy} icon={copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}>
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <label className="mt-4 flex cursor-pointer items-center gap-2 text-[0.8125rem] text-ink-2">
        <input
          type="checkbox"
          checked={acknowledged}
          onChange={(e) => setAcknowledged(e.target.checked)}
          className="h-4 w-4 accent-[var(--accent)]"
        />
        I have stored this key somewhere safe.
      </label>
    </Modal>
  );
}

export default function ApiKeysPage() {
  const toast = useToast();
  const keys = useApiKeys();
  const revoke = useRevokeApiKey();

  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [revoking, setRevoking] = useState<ApiKeyRecord | null>(null);

  return (
    <>
      <PageHeader
        title="API keys"
        description="Credentials for services and scripts. Each carries a role and is revocable at any time."
        actions={
          <Button variant="primary" icon={<KeyRound className="h-4 w-4" />} onClick={() => setCreating(true)}>
            Create key
          </Button>
        }
      />

      <div className="px-5 py-6 sm:px-8">
        <Panel>
          {keys.isError ? (
            <ErrorState error={keys.error} onRetry={() => keys.refetch()} context="API keys" />
          ) : keys.isLoading ? (
            <TableSkeleton rows={3} columns={6} />
          ) : keys.data && keys.data.length > 0 ? (
            <TableScroller>
              <Table>
                <THead>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Name</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Key</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Role</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Created</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Last used</th>
                  <th className="px-4 py-2 text-right text-xs font-medium text-ink-3">Status</th>
                </THead>
                <tbody className="divide-y divide-border">
                  {keys.data.map((key) => (
                    <tr key={key.id} className="hover:bg-surface-2">
                      <td className="px-4 py-3">
                        <p className="text-[0.875rem] font-medium text-ink">{key.label}</p>
                        {key.created_by && <p className="text-xs text-ink-3">by {key.created_by}</p>}
                      </td>
                      <td className="num px-4 py-3 text-[0.8125rem] text-ink-2">
                        {key.key_prefix ? `${key.key_prefix}…` : 'legacy key'}
                      </td>
                      <td className="px-4 py-3">
                        <RoleBadge role={key.role} />
                      </td>
                      <td className="px-4 py-3 text-[0.8125rem] text-ink-2">{formatDateTime(key.created_at)}</td>
                      <td className="px-4 py-3 text-[0.8125rem] text-ink-2">
                        {key.last_used_at ? formatRelative(key.last_used_at) : 'Never'}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {key.is_active ? (
                          <Button size="sm" variant="danger" onClick={() => setRevoking(key)}>
                            Revoke
                          </Button>
                        ) : (
                          <Badge tone="neutral">Revoked {key.revoked_at ? formatRelative(key.revoked_at) : ''}</Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            </TableScroller>
          ) : (
            <EmptyState
              icon={<KeyRound className="h-4 w-4" strokeWidth={1.6} />}
              title="No API keys"
              description="Create a key to let a script or service call the LEDGR API."
              action={
                <Button size="sm" variant="primary" onClick={() => setCreating(true)}>
                  Create key
                </Button>
              }
            />
          )}
        </Panel>
      </div>

      <CreateKeyModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(key) => {
          setCreating(false);
          setCreated(key);
        }}
      />
      <NewKeyModal created={created} onClose={() => setCreated(null)} />

      <ConfirmDialog
        open={Boolean(revoking)}
        title="Revoke this API key?"
        description={
          revoking
            ? `"${revoking.label}" stops working immediately. Anything still using it will start failing with 401. This cannot be undone.`
            : undefined
        }
        tone="danger"
        confirmLabel="Revoke key"
        busy={revoke.isPending}
        onClose={() => setRevoking(null)}
        onConfirm={() => {
          if (!revoking) return;
          revoke.mutate(revoking.id, {
            onSuccess: () => {
              toast.success('API key revoked', revoking.label);
              setRevoking(null);
            },
            onError: (error) => {
              toast.apiError(error);
              setRevoking(null);
            },
          });
        }}
      />
    </>
  );
}
