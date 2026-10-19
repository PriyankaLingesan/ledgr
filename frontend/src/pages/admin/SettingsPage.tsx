/**
 * Organization settings. Administrator-only (`org:manage`).
 *
 * The approval threshold is the maker-checker policy: a transaction at or
 * above it cannot be posted directly (the server answers 409
 * `approval_required`) - it must be proposed as a draft and approved by a
 * different person. The threshold is compared in minor units of each
 * transaction's own currency.
 */

import { useEffect, useState, type FormEvent } from 'react';

import { PageHeader } from '../../components/layout/AppShell';
import { Badge, Button, Field, Input, Panel, PanelHeader, Skeleton } from '../../components/ui/primitives';
import { ErrorState } from '../../components/ui/Table';
import { formatMinor, parseAmountToMinor } from '../../lib/format';
import { useOrganization, useUpdateOrganization } from '../../lib/opsQueries';
import { useCurrencies } from '../../lib/queries';
import { useToast } from '../../lib/toast';

export default function SettingsPage() {
  const toast = useToast();
  const org = useOrganization();
  const { data: currencies } = useCurrencies();
  const update = useUpdateOrganization();

  const [name, setName] = useState('');
  const [threshold, setThreshold] = useState('');

  const data = org.data;
  useEffect(() => {
    if (!data) return;
    setName(data.name);
    setThreshold(
      data.approval_threshold_minor === null
        ? ''
        : formatMinor(data.approval_threshold_minor, data.base_currency, currencies).replace(/,/g, ''),
    );
  }, [data, currencies]);

  const parsedThreshold = data && threshold.trim() ? parseAmountToMinor(threshold, data.base_currency, currencies) : null;
  const thresholdInvalid = Boolean(threshold.trim()) && (parsedThreshold === null || parsedThreshold <= 0);

  const saveName = (event: FormEvent) => {
    event.preventDefault();
    update.mutate(
      { name: name.trim() },
      {
        onSuccess: () => toast.success('Organization name saved'),
        onError: (error) => toast.apiError(error),
      },
    );
  };

  const saveThreshold = (event: FormEvent) => {
    event.preventDefault();
    if (parsedThreshold === null || parsedThreshold <= 0) return;
    update.mutate(
      { approval_threshold_minor: parsedThreshold },
      {
        onSuccess: () =>
          toast.success(
            'Approval policy saved',
            `Transactions of ${threshold} ${data?.base_currency} or more now require a second approver.`,
          ),
        onError: (error) => toast.apiError(error),
      },
    );
  };

  return (
    <>
      <PageHeader title="Organization" description="Name and maker-checker policy" />

      <div className="max-w-3xl space-y-6 px-5 py-6 sm:px-8">
        {org.isError && (
          <Panel>
            <ErrorState error={org.error} onRetry={() => org.refetch()} context="organization settings" />
          </Panel>
        )}

        <Panel>
          <PanelHeader title="Organization" />
          {org.isLoading ? (
            <div className="p-4">
              <Skeleton className="h-16 w-full" />
            </div>
          ) : (
            <form onSubmit={saveName} className="flex flex-col gap-4 px-4 py-4">
              <Field label="Name" htmlFor="org-name">
                <Input id="org-name" value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <div className="flex items-center justify-between">
                <p className="text-[0.8125rem] text-ink-2">
                  Base currency: <Badge tone="neutral">{data?.base_currency}</Badge>
                </p>
                <Button
                  type="submit"
                  variant="primary"
                  disabled={update.isPending || !name.trim() || name.trim() === data?.name}
                >
                  Save
                </Button>
              </div>
            </form>
          )}
        </Panel>

        <Panel>
          <PanelHeader
            title="Approval threshold"
            description="Maker-checker: large transactions need a second person"
          />
          {org.isLoading ? (
            <div className="p-4">
              <Skeleton className="h-16 w-full" />
            </div>
          ) : (
            <form onSubmit={saveThreshold} className="flex flex-col gap-4 px-4 py-4">
              <Field
                label={`Require approval at or above (${data?.base_currency})`}
                htmlFor="threshold"
                hint="Leave empty for no threshold. Below it, finance users can still post directly; at or above it they must submit a draft for another person to approve."
                error={thresholdInvalid ? 'Enter a positive amount, e.g. 10000.00' : null}
              >
                <Input
                  id="threshold"
                  inputMode="decimal"
                  value={threshold}
                  placeholder="No threshold"
                  onChange={(e) => setThreshold(e.target.value)}
                />
              </Field>
              <div className="flex items-center justify-between gap-2">
                <p className="text-[0.8125rem] text-ink-3">
                  {data?.approval_threshold_minor === null
                    ? 'Currently: direct posting is never blocked by amount.'
                    : `Currently: ${formatMinor(data?.approval_threshold_minor ?? 0, data?.base_currency ?? 'USD', currencies, true)} or more requires approval.`}
                </p>
                <div className="flex gap-2">
                  {data?.approval_threshold_minor !== null && (
                    <Button
                      variant="ghost"
                      disabled={update.isPending}
                      onClick={() =>
                        update.mutate(
                          { clear_approval_threshold: true },
                          {
                            onSuccess: () => toast.success('Approval threshold removed'),
                            onError: (error) => toast.apiError(error),
                          },
                        )
                      }
                    >
                      Remove threshold
                    </Button>
                  )}
                  <Button
                    type="submit"
                    variant="primary"
                    disabled={update.isPending || thresholdInvalid || !threshold.trim()}
                  >
                    Save policy
                  </Button>
                </div>
              </div>
            </form>
          )}
        </Panel>
      </div>
    </>
  );
}
