import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ToastProvider } from '../../lib/toast';
import { ImportWizard, parseCsvText } from './ImportWizard';

describe('parseCsvText', () => {
  it('handles quoted commas, escaped quotes and CRLF line endings', () => {
    const rows = parseCsvText('id,desc\r\nA1,"Smith, J ""VIP"""\r\nA2,plain\r\n');
    expect(rows).toEqual([
      ['id', 'desc'],
      ['A1', 'Smith, J "VIP"'],
      ['A2', 'plain'],
    ]);
  });

  it('skips blank lines', () => {
    expect(parseCsvText('a,b\n\n\nc,d\n')).toEqual([
      ['a', 'b'],
      ['c', 'd'],
    ]);
  });
});

function renderWizard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <ImportWizard open onClose={() => undefined} />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('ImportWizard', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify([{ code: 'USD', name: 'US Dollar', exponent: 2 }]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  const csv = (text: string) => userEvent.setup().click(screen.getByLabelText('CSV')).then(() => userEvent.paste(text));

  it('keeps Import disabled and explains every invalid row before anything is sent', async () => {
    renderWizard();
    await userEvent.type(screen.getByLabelText(/Source system/), 'BANK');
    await csv(
      'external_id,amount,currency,direction,timestamp\nPAY-1,12.50,USD,debit,2026-09-08T12:00:00Z\nPAY-2,abc,USD,debit,2026-09-08T12:00:00Z',
    );

    await waitFor(() => expect(screen.getByText(/1 row\(s\) need fixing/)).toBeTruthy());
    expect(screen.getByText(/bad amount "abc"/)).toBeTruthy();
    const importButton = screen.getByRole('button', { name: /^Import/ }) as HTMLButtonElement;
    expect(importButton.disabled).toBe(true);
  });

  it('enables Import once every row validates and auto-maps recognisable headers', async () => {
    renderWizard();
    await userEvent.type(screen.getByLabelText(/Source system/), 'BANK');
    await csv(
      'external_id,amount,currency,direction,timestamp\nPAY-1,12.50,USD,debit,2026-09-08T12:00:00Z\nPAY-2,3.00,USD,credit,2026-09-09T08:00:00Z',
    );

    await waitFor(() => expect(screen.getByText(/2 row\(s\) valid/)).toBeTruthy());
    expect((screen.getByLabelText(/External id/) as HTMLSelectElement).value).toBe('0');
    const importButton = screen.getByRole('button', { name: /^Import 2 records/ }) as HTMLButtonElement;
    expect(importButton.disabled).toBe(false);
  });

  it('flags missing required column mappings instead of guessing', async () => {
    renderWizard();
    await userEvent.type(screen.getByLabelText(/Source system/), 'BANK');
    await csv('colA,colB\nx,y');
    await waitFor(() => expect(screen.getByText(/Map the required columns/)).toBeTruthy());
  });
});
