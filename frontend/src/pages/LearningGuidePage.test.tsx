import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '../lib/auth';
import type { Guide } from '../lib/demo';
import { EXERCISES } from '../lib/learningContent';
import type { CurrentUser, Permission } from '../lib/opsTypes';
import { ToastProvider } from '../lib/toast';
import LearningGuidePage from './LearningGuidePage';

const WRITE: Permission[] = ['dashboard:read', 'investigations:write', 'transactions:post'];

function user(over: Partial<CurrentUser> = {}): CurrentUser {
  return {
    id: 'u1',
    kind: 'user',
    email: 'meera.krishnan@meridian-demo.test',
    name: 'Meera Krishnan',
    role: 'OWNER',
    permissions: WRITE,
    organization_id: 'o1',
    organization_name: 'Meridian Commerce Pvt. Ltd.',
    organization_demo_scenario: 'meridian-v1',
    csrf_token: 'csrf',
    ...over,
  };
}

const ANSWER_KINDS = ['ex1', 'ex2', 'ex3', 'ex10', 'ex11'];

function guide(completed: string[] = []): Guide {
  return {
    scenario: 'meridian-v1',
    organization_name: 'Meridian Commerce Pvt. Ltd.',
    as_of: '2026-10-16',
    approval_threshold_minor: 100_000_000,
    statement: { source_system: 'DEMOBANK-CA-0142', record_count: 24, filename: 'meridian_bank_statement_2.csv' },
    ai_available: false,
    personas: [
      {
        key: 'owner',
        email: 'meera.krishnan@meridian-demo.test',
        full_name: 'Meera Krishnan',
        role: 'OWNER',
        title: 'Director',
        responsibilities: 'Owns the organization.',
        active: true,
      },
      {
        key: 'maker',
        email: 'kavya.menon@meridian-demo.test',
        full_name: 'Kavya Menon',
        role: 'FINANCE',
        title: 'Accounts Executive',
        responsibilities: 'Proposes.',
        active: false,
      },
    ],
    exercises: EXERCISES.map((e) => ({
      id: e.id,
      kind: ANSWER_KINDS.includes(e.id) ? ('answer' as const) : ('state' as const),
      completed: completed.includes(e.id),
      facts: {
        opening_reference: 'MCP-OB-0001',
        customer: 'Sundaram Hardware & Tools Pvt Ltd',
        amount_minor: 29_500_000,
        term: 'SwiftHaul',
        from: '2026-08-17',
        to: '2026-10-16',
        record_count: 24,
        source_system: 'DEMOBANK-CA-0142',
        fee_external_id: 'DNBF26101442589',
        external_id: 'DNBN26090741787',
        candidate_references: ['MCP-RC-0048', 'MCP-RC-0049'],
        ledger_reference: 'MCP-PP-0028',
        threshold_minor: 100_000_000,
        reversal_reference: 'MCP-RV-0001',
        transaction_reference: 'MCP-RC-0049',
      },
    })),
    completed_count: completed.length,
    total_count: EXERCISES.length,
  };
}

interface Server {
  completed: string[];
  verifyAnswers: { id: string; body: unknown }[];
  verdicts: Record<string, { passed: boolean; message: string }>;
  me: CurrentUser;
}

function install(server: Server) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const json = (status: number, body: unknown) =>
      new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    if (url.endsWith('/auth/me')) return json(200, server.me);
    if (url.endsWith('/demo/guide')) return json(200, guide(server.completed));
    if (url.endsWith('/ai/status')) {
      return json(200, { available: false, provider: null, reason: 'No AI provider is configured.' });
    }
    const verify = /\/demo\/guide\/(ex\d+)\/verify$/.exec(url);
    if (verify && init?.method === 'POST') {
      const id = verify[1];
      server.verifyAnswers.push({ id, body: JSON.parse(String(init.body)) });
      const verdict = server.verdicts[id] ?? { passed: false, message: 'Not yet.' };
      if (verdict.passed && !server.completed.includes(id)) server.completed.push(id);
      return json(200, { exercise_id: id, ...verdict });
    }
    return json(404, { error: { code: 'not_found', message: 'nope', details: {}, request_id: null } });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <AuthProvider>
          <MemoryRouter>
            <LearningGuidePage />
          </MemoryRouter>
        </AuthProvider>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

describe('LearningGuidePage', () => {
  let server: Server;
  beforeEach(() => {
    server = { completed: [], verifyAnswers: [], verdicts: {}, me: user() };
  });
  afterEach(() => vi.unstubAllGlobals());

  it('lists all twelve exercises with progress taken from the server', async () => {
    server.completed = ['ex1', 'ex5'];
    install(server);
    renderPage();
    expect(await screen.findByText(/Your progress: 2 of 12 exercises/)).toBeTruthy();
    const list = screen.getByRole('list', { name: 'Exercises' });
    expect(list.querySelectorAll(':scope > li').length).toBe(12);
    expect(screen.getAllByText('Completed').length).toBe(2);
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('2');
    expect(screen.getByText('Kavya Menon')).toBeTruthy();
    expect(screen.getByText(/Not set up/)).toBeTruthy();
  });

  it('never marks an exercise complete just because it was opened', async () => {
    const fetchMock = install(server);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Search and filter transactions/ }));
    fireEvent.click(screen.getByRole('button', { name: /Resolve an ambiguous match/ }));
    expect(screen.queryAllByText('Completed').length).toBe(0);
    expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/verify'))).toBe(false);
  });

  it('sends your answer to the server and shows its verdict; progress follows the server', async () => {
    server.verdicts = { ex1: { passed: false, message: 'Not quite. Open the opening-balances transaction.' } };
    install(server);
    renderPage();
    const input = await screen.findByLabelText(/Opening debit to Inventory/);
    const check = screen.getByRole('button', { name: 'Check my work' });
    expect((check as HTMLButtonElement).disabled).toBe(true); // nothing typed yet
    fireEvent.change(input, { target: { value: '123' } });
    fireEvent.click(check);
    expect(await screen.findByText(/Not quite/)).toBeTruthy();
    expect(server.verifyAnswers).toEqual([{ id: 'ex1', body: { answer: '123' } }]);
    expect(screen.queryByText('Completed')).toBeNull();

    server.verdicts.ex1 = { passed: true, message: 'Correct: that is the Inventory debit.' };
    fireEvent.change(input, { target: { value: '9500000' } });
    fireEvent.click(screen.getByRole('button', { name: 'Check my work' }));
    expect(await screen.findByText(/Correct: that is the Inventory debit/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/Your progress: 1 of 12/)).toBeTruthy());
    expect(screen.getAllByText('Completed').length).toBe(1);
  });

  it('shows exercise facts from the server in rupees, but never an expected answer', async () => {
    install(server);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Find a customer receipt/ }));
    expect(screen.getAllByText(/₹2,95,000\.00/).length).toBeGreaterThan(0); // Indian digit grouping
    expect(document.body.textContent).not.toMatch(/MCP-RC-0022/);
  });

  it('explains honestly that the AI exercise needs a live model', async () => {
    install(server);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Use the AI copilot/ }));
    expect((await screen.findAllByText(/No AI provider is configured/)).length).toBeGreaterThan(0);
    expect(screen.getByText(/cannot be completed here/)).toBeTruthy();
  });

  it('keeps the idempotency lab disabled for a role that cannot post', async () => {
    server.me = user({ role: 'OPERATIONS', permissions: ['dashboard:read', 'investigations:write'] });
    install(server);
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Retry a posting safely/ }));
    expect(await screen.findByText(/cannot post transactions/)).toBeTruthy();
    const send = screen.getByRole('button', { name: /1\. Send the posting/ }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
  });

  it('lets a read-only role read the guide but not record progress', async () => {
    server.me = user({ role: 'AUDITOR', permissions: ['dashboard:read'] });
    install(server);
    renderPage();
    expect(await screen.findByText(/Your progress/)).toBeTruthy();
    const check = screen.getByRole('button', { name: 'Check my work' }) as HTMLButtonElement;
    expect(check.disabled).toBe(true);
    expect(screen.getByText(/read-only/)).toBeTruthy();
  });

  it('says plainly that a real organization has no guide, without calling the guide endpoint', async () => {
    server.me = user({ organization_demo_scenario: null, organization_name: 'Real Co' });
    const fetchMock = install(server);
    renderPage();
    expect(await screen.findByText(/no learning guide/i)).toBeTruthy();
    expect(fetchMock.mock.calls.some(([u]) => String(u).endsWith('/demo/guide'))).toBe(false);
  });
});
