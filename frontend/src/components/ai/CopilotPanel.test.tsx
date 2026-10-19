import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import type { CopilotAnswer } from '../../lib/opsTypes';
import { ApiError } from '../../lib/api';
import { CopilotAnswerView, copilotErrorMessage } from './CopilotPanel';

const answer: CopilotAnswer = {
  task: 'SUMMARIZE',
  summary: 'The provider reports 5000.00 USD; the ledger booked 4500.00 USD.',
  verified_facts: [{ statement: 'The case is open.', source: 'case:INV-0001' }],
  hypotheses: [
    { statement: 'A fee may have been netted.', basis: 'amounts differ by 500.00' },
    { statement: 'The CFO approved it.', basis: 'unverified: the model cited a source that is not in the case data' },
  ],
  missing_evidence: ['Provider settlement report'],
  suggested_next_steps: ['Request the payout breakdown'],
  document: null,
  evidence_sufficient: false,
  sources: [{ id: 'case:INV-0001', label: 'Case INV-0001', link: '/investigations/abc' }],
  ungrounded_claims_moved: 1,
  requires_review: true,
  disclaimer: 'AI-generated draft. Verify against the linked records before relying on it.',
};

describe('CopilotAnswerView', () => {
  it('labels the output as an AI draft that needs review and flags incomplete evidence', () => {
    render(
      <MemoryRouter>
        <CopilotAnswerView answer={answer} />
      </MemoryRouter>,
    );
    expect(screen.getByText('AI draft')).toBeTruthy();
    expect(screen.getByText('Requires your review')).toBeTruthy();
    expect(screen.getByText('Evidence incomplete')).toBeTruthy();
  });

  it('keeps verified facts (with a link to their source) apart from unverified hypotheses', () => {
    render(
      <MemoryRouter>
        <CopilotAnswerView answer={answer} />
      </MemoryRouter>,
    );
    expect(screen.getByText('Verified against records')).toBeTruthy();
    expect(screen.getByText('Hypotheses - not verified')).toBeTruthy();
    const link = screen.getByRole('link', { name: /Case INV-0001/ });
    expect(link.getAttribute('href')).toBe('/app/investigations/abc');
    expect(screen.getByText(/A fee may have been netted/)).toBeTruthy();
    expect(screen.getByText(/1 claim cited a source that is not in this case/)).toBeTruthy();
  });

  it('offers no control that could change anything', () => {
    render(
      <MemoryRouter>
        <CopilotAnswerView answer={{ ...answer, document: 'Draft text' }} />
      </MemoryRouter>,
    );
    const buttons = screen.getAllByRole('button').map((b) => b.textContent);
    expect(buttons).toEqual(['Copy']);
  });
});

describe('copilotErrorMessage', () => {
  const err = (code: string) => new ApiError(503, { code, message: 'x', details: {}, request_id: null });

  it('explains unavailable, invalid and throttled responses in plain words', () => {
    expect(copilotErrorMessage(err('ai_unavailable'))).toMatch(/unavailable/);
    expect(copilotErrorMessage(err('ai_response_invalid'))).toMatch(/discarded/);
    expect(copilotErrorMessage(err('rate_limited'))).toMatch(/too quickly/);
  });
});
