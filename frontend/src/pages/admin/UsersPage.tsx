/**
 * Users & roles. Administrator-only (`users:manage`).
 *
 * Roles are enforced by the server on every request; changing one here takes
 * effect on that person's next request. Deactivating a user ends their
 * sessions immediately, and the last active administrator can never be
 * demoted or deactivated (the server refuses).
 */

import { Lock, UserPlus } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { PageHeader } from '../../components/layout/AppShell';
import { ROLE_DESCRIPTIONS, RoleBadge } from '../../components/ops/atoms';
import { ConfirmDialog } from '../../components/ui/ConfirmDialog';
import { Modal } from '../../components/ui/Modal';
import { Badge, Button, Field, Input, Panel, Select } from '../../components/ui/primitives';
import { EmptyState, ErrorState, Table, TableScroller, TableSkeleton, THead } from '../../components/ui/Table';
import { useAuth } from '../../lib/auth';
import { formatDateTime } from '../../lib/format';
import { useCreateUser, useResetPassword, useUpdateUser, useUsers } from '../../lib/opsQueries';
import { ALL_ROLES, canManageRole, type ManagedUser, type UserRole } from '../../lib/opsTypes';
import { useToast } from '../../lib/toast';

const ROLES = ALL_ROLES;

function AddUserModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast();
  const { user: me } = useAuth();
  const assignable = ROLES.filter((r) => canManageRole(me?.role, r));
  const create = useCreateUser();
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<UserRole>('FINANCE');

  const reset = () => {
    setFullName('');
    setEmail('');
    setPassword('');
    setRole('FINANCE');
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    create.mutate(
      { full_name: fullName.trim(), email: email.trim(), password, role },
      {
        onSuccess: (user) => {
          toast.success('User created', `${user.email} can now sign in as ${user.role.toLowerCase()}.`);
          reset();
          onClose();
        },
        onError: (error) => toast.apiError(error),
      },
    );
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Add a user"
      description="They sign in with this email and the temporary password you set."
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <Field label="Full name" htmlFor="new-name" required>
          <Input id="new-name" value={fullName} onChange={(e) => setFullName(e.target.value)} autoFocus />
        </Field>
        <Field label="Email" htmlFor="new-email" required>
          <Input id="new-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field
          label="Temporary password"
          htmlFor="new-password"
          required
          hint="At least 10 characters. Share it securely; the person should change it after signing in."
        >
          <Input
            id="new-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
        <Field label="Role" htmlFor="new-role" hint={ROLE_DESCRIPTIONS[role]}>
          <Select id="new-role" value={role} onChange={(e) => setRole(e.target.value as UserRole)}>
            {assignable.map((r) => (
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
          <Button
            type="submit"
            variant="primary"
            disabled={create.isPending || !fullName.trim() || !email.trim() || password.length < 10}
          >
            Create user
          </Button>
        </div>
      </form>
    </Modal>
  );
}

function ResetPasswordModal({ user, onClose }: { user: ManagedUser | null; onClose: () => void }) {
  const toast = useToast();
  const reset = useResetPassword();
  const [password, setPassword] = useState('');

  return (
    <ConfirmDialog
      open={Boolean(user)}
      title={`Reset password for ${user?.email ?? ''}`}
      description="All of their current sessions end immediately and any lockout is cleared."
      confirmLabel="Reset password"
      busy={reset.isPending}
      confirmDisabled={password.length < 10}
      onClose={() => {
        setPassword('');
        onClose();
      }}
      onConfirm={() => {
        if (!user) return;
        reset.mutate(
          { id: user.id, new_password: password },
          {
            onSuccess: () => {
              toast.success('Password reset', `${user.email} must sign in again.`);
              setPassword('');
              onClose();
            },
            onError: (error) => toast.apiError(error),
          },
        );
      }}
    >
      <Field label="New temporary password" htmlFor="reset-password" hint="At least 10 characters.">
        <Input
          id="reset-password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
        />
      </Field>
    </ConfirmDialog>
  );
}

export default function UsersPage() {
  const { user: me } = useAuth();
  const toast = useToast();
  const users = useUsers();
  const update = useUpdateUser();

  const [adding, setAdding] = useState(false);
  const [resetting, setResetting] = useState<ManagedUser | null>(null);
  const [pendingRole, setPendingRole] = useState<{ user: ManagedUser; role: UserRole } | null>(null);
  const [pendingActive, setPendingActive] = useState<ManagedUser | null>(null);

  return (
    <>
      <PageHeader
        title="Users & roles"
        description="Who can sign in, and what each role is allowed to do"
        actions={
          <Button variant="primary" icon={<UserPlus className="h-4 w-4" />} onClick={() => setAdding(true)}>
            Add user
          </Button>
        }
      />

      <div className="space-y-6 px-5 py-6 sm:px-8">
        <Panel>
          {users.isError ? (
            <ErrorState error={users.error} onRetry={() => users.refetch()} context="users" />
          ) : users.isLoading ? (
            <TableSkeleton rows={4} columns={5} />
          ) : users.data && users.data.length > 0 ? (
            <TableScroller>
              <Table>
                <THead>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">User</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Role</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Status</th>
                  <th className="px-4 py-2 text-xs font-medium text-ink-3">Last sign-in</th>
                  <th className="px-4 py-2 text-right text-xs font-medium text-ink-3">Actions</th>
                </THead>
                <tbody className="divide-y divide-border">
                  {users.data.map((u) => {
                    const self = u.id === me?.id;
                    const manageable = canManageRole(me?.role, u.role);
                    return (
                      <tr key={u.id} className="hover:bg-surface-2">
                        <td className="px-4 py-3">
                          <p className="text-[0.875rem] font-medium text-ink">
                            {u.full_name} {self && <span className="text-xs text-ink-3">(you)</span>}
                          </p>
                          <p className="text-[0.8125rem] text-ink-2">{u.email}</p>
                        </td>
                        <td className="px-4 py-3">
                          <Select
                            aria-label={`Role for ${u.email}`}
                            className="h-8 w-36"
                            value={u.role}
                            disabled={!manageable}
                            title={manageable ? undefined : `Only an owner can change ${u.role} users`}
                            onChange={(e) => setPendingRole({ user: u, role: e.target.value as UserRole })}
                          >
                            {ROLES.filter((r) => r === u.role || canManageRole(me?.role, r)).map((r) => (
                              <option key={r} value={r}>
                                {r}
                              </option>
                            ))}
                          </Select>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <Badge tone={u.is_active ? 'positive' : 'neutral'}>
                              {u.is_active ? 'Active' : 'Deactivated'}
                            </Badge>
                            {u.locked && (
                              <Badge tone="warning">
                                <Lock className="h-3 w-3" /> Locked
                              </Badge>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3 text-[0.8125rem] text-ink-2">
                          {u.last_login_at ? formatDateTime(u.last_login_at) : 'Never'}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex justify-end gap-1.5">
                            <Button size="sm" disabled={!manageable} onClick={() => setResetting(u)}>
                              Reset password
                            </Button>
                            <Button
                              size="sm"
                              variant={u.is_active ? 'danger' : 'secondary'}
                              disabled={!manageable || (self && u.is_active)}
                              title={self && u.is_active ? 'You cannot deactivate yourself' : undefined}
                              onClick={() => setPendingActive(u)}
                            >
                              {u.is_active ? 'Deactivate' : 'Reactivate'}
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </Table>
            </TableScroller>
          ) : (
            <EmptyState title="No users" />
          )}
        </Panel>

        <Panel>
          <div className="border-b border-border px-4 py-3.5">
            <h2 className="text-base font-semibold text-ink">What each role can do</h2>
            <p className="mt-0.5 text-[0.8125rem] text-ink-2">
              Enforced by the server on every request - hiding a button in the console is only a convenience.
            </p>
          </div>
          <ul className="divide-y divide-border">
            {ROLES.map((role) => (
              <li key={role} className="flex items-start gap-3 px-4 py-3">
                <div className="w-28 shrink-0">
                  <RoleBadge role={role} />
                </div>
                <p className="text-[0.875rem] text-ink-2">{ROLE_DESCRIPTIONS[role]}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <AddUserModal open={adding} onClose={() => setAdding(false)} />
      <ResetPasswordModal user={resetting} onClose={() => setResetting(null)} />

      <ConfirmDialog
        open={Boolean(pendingRole)}
        title="Change role?"
        description={
          pendingRole
            ? `${pendingRole.user.email} will become ${pendingRole.role}. This takes effect on their next request.`
            : undefined
        }
        confirmLabel="Change role"
        busy={update.isPending}
        onClose={() => setPendingRole(null)}
        onConfirm={() => {
          if (!pendingRole) return;
          update.mutate(
            { id: pendingRole.user.id, role: pendingRole.role },
            {
              onSuccess: () => {
                toast.success('Role updated');
                setPendingRole(null);
              },
              onError: (error) => {
                toast.apiError(error);
                setPendingRole(null);
              },
            },
          );
        }}
      />

      <ConfirmDialog
        open={Boolean(pendingActive)}
        title={pendingActive?.is_active ? 'Deactivate this user?' : 'Reactivate this user?'}
        description={
          pendingActive?.is_active
            ? `${pendingActive.email} will be signed out immediately and unable to sign in.`
            : pendingActive
              ? `${pendingActive.email} will be able to sign in again.`
              : undefined
        }
        tone={pendingActive?.is_active ? 'danger' : 'primary'}
        confirmLabel={pendingActive?.is_active ? 'Deactivate' : 'Reactivate'}
        busy={update.isPending}
        onClose={() => setPendingActive(null)}
        onConfirm={() => {
          if (!pendingActive) return;
          update.mutate(
            { id: pendingActive.id, is_active: !pendingActive.is_active },
            {
              onSuccess: () => {
                toast.success(pendingActive.is_active ? 'User deactivated' : 'User reactivated');
                setPendingActive(null);
              },
              onError: (error) => {
                toast.apiError(error);
                setPendingActive(null);
              },
            },
          );
        }}
      />
    </>
  );
}
