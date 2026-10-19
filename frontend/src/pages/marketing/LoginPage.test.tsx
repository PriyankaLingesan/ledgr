import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '../../lib/auth';
import { ToastProvider } from '../../lib/toast';
import LoginPage from './LoginPage';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function stubApi(config: { signup_enabled: boolean; signup_code_required: boolean }) {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET' });
      if (url.endsWith('/auth/me')) return json(401, { error: { code: 'authentication_required', message: 'no', details: {}, request_id: null } });
      if (url.endsWith('/auth/config')) return json(200, config);
      if (url.endsWith('/auth/login')) return json(401, { error: { code: 'invalid_credentials', message: 'the email or password is incorrect', details: {}, request_id: null } });
      return json(404, { error: { code: 'not_found', message: 'nope', details: {}, request_id: null } });
    }),
  );
  return calls;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>
          <AuthProvider>
            <LoginPage />
          </AuthProvider>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('LoginPage', () => {
  beforeEach(() => undefined);
  afterEach(() => vi.unstubAllGlobals());

  it('shows the server\'s generic failure message and clears the password', async () => {
    stubApi({ signup_enabled: true, signup_code_required: false });
    renderPage();
    await userEvent.type(await screen.findByLabelText(/Email/), 'someone@example.com');
    await userEvent.type(screen.getByLabelText(/Password/), 'wrong-password');
    await userEvent.click(screen.getByRole('button', { name: /Sign in/ }));

    expect(await screen.findByRole('alert')).toHaveProperty('textContent', expect.stringContaining('the email or password is incorrect'));
    expect((screen.getByLabelText(/Password/) as HTMLInputElement).value).toBe('');
  });

  it('keeps the submit button disabled until both fields are filled', async () => {
    stubApi({ signup_enabled: true, signup_code_required: false });
    renderPage();
    const button = (await screen.findByRole('button', { name: /^Sign in$/ })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    await userEvent.type(screen.getByLabelText(/Email/), 'a@b.co');
    await userEvent.type(screen.getByLabelText(/Password/), 'x');
    expect(button.disabled).toBe(false);
  });

  it('offers account creation with password-length validation and an invite code when required', async () => {
    stubApi({ signup_enabled: true, signup_code_required: true });
    renderPage();
    await userEvent.click(await screen.findByRole('tab', { name: /Create account/ }));

    await userEvent.type(screen.getByLabelText(/Organization name/), 'Acme');
    await userEvent.type(screen.getByLabelText(/Your name/), 'Ada');
    await userEvent.type(screen.getByLabelText(/^Email/), 'ada@acme.test');
    await userEvent.type(screen.getByLabelText(/^Password/), 'short');
    expect(screen.getByText('Use at least 10 characters.')).toBeTruthy();
    const submit = screen.getByRole('button', { name: /Create organization/ }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);

    await userEvent.clear(screen.getByLabelText(/^Password/));
    await userEvent.type(screen.getByLabelText(/^Password/), 'a-long-enough-passphrase');
    expect(submit.disabled).toBe(true); // still needs the signup code
    await userEvent.type(screen.getByLabelText(/Signup code/), 'invite');
    expect(submit.disabled).toBe(false);
  });

  it('hides account creation when signup is disabled', async () => {
    stubApi({ signup_enabled: false, signup_code_required: false });
    renderPage();
    await waitFor(() => expect(screen.getByText(/Self-service signup is turned off/)).toBeTruthy());
    expect(screen.queryByRole('tab', { name: /Create account/ })).toBeNull();
  });

  it('reports an unreachable API with a retry', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('failed'))));
    renderPage();
    expect(await screen.findByText(/Cannot reach the LEDGR API/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
  });
});
