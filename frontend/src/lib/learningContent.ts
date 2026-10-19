/**
 * The teaching text of the Demo Learning Guide.
 *
 * Only prose lives here. What counts as "done" - and every expected answer - is decided by
 * the server from the real state of the books (`/demo/guide/{id}/verify`), so nothing in this
 * file can be edited to fake progress, and no answer is shipped to the browser.
 */

import { formatMinor } from './format';

type Facts = Record<string, unknown>;

export interface ExerciseContent {
  id: string;
  title: string;
  /** Who should do this (the demo logins, see the people table). */
  who: string;
  context: (f: Facts) => string;
  task: (f: Facts) => string[];
  where: { label: string; to: string }[];
  inspect: (f: Facts) => string[];
  expected: (f: Facts) => string;
  concept: string;
  /** Present for "find something" exercises: what to type in. */
  answer?: { label: string; placeholder: string; inputMode?: 'numeric' | 'text' };
  /** Extra widget shown inside the exercise. */
  tool?: 'idempotency-lab' | 'sample-statement' | 'ai';
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const num = (v: unknown): number => (typeof v === 'number' ? v : 0);
const inr = (minor: unknown): string => `₹${formatMinor(num(minor), 'INR')}`;

export const EXERCISES: ExerciseContent[] = [
  {
    id: 'ex1',
    title: 'Understand the chart of accounts and the opening position',
    who: 'Anyone',
    context: () =>
      'Meridian trades industrial supplies. Before anything is bought or sold, its books start from an opening position: what the company owned and owed on day one.',
    task: (f) => [
      'Open Accounts and read the chart: assets, liabilities, equity, revenue and expenses.',
      `Open the opening-balances transaction (${str(f.opening_reference)}) and look at every line.`,
      'Find the debit posted to "Inventory - Trading Stock" and enter its amount in rupees.',
    ],
    where: [
      { label: 'Accounts', to: '/app/accounts' },
      { label: 'Transactions', to: '/app/transactions' },
    ],
    inspect: () => [
      'Total debits equal total credits - the opening entry balances.',
      'Assets and expenses increase with debits; liabilities, equity and revenue increase with credits.',
    ],
    expected: () =>
      'One transaction with ten lines whose debits and credits are the same total. The inventory line is a debit.',
    concept:
      'Double-entry bookkeeping: every transaction is a set of debits and credits that must balance. An account\'s balance is never stored as a free number - LEDGR derives it from its entries.',
    answer: { label: 'Opening debit to Inventory (₹)', placeholder: 'e.g. 1234567', inputMode: 'numeric' },
  },
  {
    id: 'ex2',
    title: 'Find a customer receipt and read its debit and credit',
    who: 'Anyone',
    context: (f) =>
      `${str(f.customer)} paid ${inr(f.amount_minor)} against an invoice some months ago. You need to find that receipt in a year of postings.`,
    task: (f) => [
      `Search Transactions for "${str(f.customer).split(' ')[0]}".`,
      `Pick the customer receipt for exactly ${inr(f.amount_minor)} (an invoice has the same customer but a different amount).`,
      'Enter its transaction reference.',
    ],
    where: [{ label: 'Transactions', to: '/app/transactions' }],
    inspect: () => [
      'The receipt debits a bank account and credits Accounts Receivable.',
      'The invoice it settles is named in the description.',
    ],
    expected: () => 'A reference of the form MCP-RC-nnnn. Its two lines are a bank debit and a receivables credit.',
    concept:
      'A receipt does not create revenue - the sale already did, when the invoice was raised (debit receivables, credit sales). The receipt only moves value from "owed to us" to "in the bank".',
    answer: { label: 'Receipt reference', placeholder: 'MCP-RC-0000' },
  },
  {
    id: 'ex3',
    title: 'Search and filter transactions',
    who: 'Anyone',
    context: (f) =>
      `Your manager asks how many freight payments to "${str(f.term)}" were posted between ${str(f.from)} and ${str(f.to)}.`,
    task: (f) => [
      `On Transactions, search for "${str(f.term)}".`,
      `Set the date range from ${str(f.from)} to ${str(f.to)} and the status to Posted.`,
      'Read the count under the page title and enter it.',
    ],
    where: [{ label: 'Transactions', to: '/app/transactions' }],
    inspect: () => ['The count changes as you add each filter - search narrows, dates narrow, status narrows.'],
    expected: () => 'A small whole number (a few freight payments per month).',
    concept:
      'Filters never hide records from the ledger - they only change the view. Export CSV on the same page exports exactly what the filters show.',
    answer: { label: 'Number of transactions', placeholder: 'e.g. 6', inputMode: 'numeric' },
  },
  {
    id: 'ex4',
    title: 'Retry a posting safely (idempotency)',
    who: 'Finance (Kavya) or the owner',
    context: () =>
      'A network error hides the response to a payment request. The client retries. Without protection that could pay twice.',
    task: () => [
      'Use the Idempotency lab below to send a posting with a key.',
      'Send the exact same request again with the same key.',
      'Then send the same key with a different amount, and read what happens.',
    ],
    where: [
      { label: 'Transactions', to: '/app/transactions' },
      { label: 'Audit log', to: '/app/audit' },
    ],
    inspect: () => [
      'The second send returns the original transaction and is marked as a replay; only one transaction exists.',
      'The changed request is refused: a key belongs to one specific request.',
      'The audit log records the replay.',
    ],
    expected: () => 'One ledger transaction, one replay event in the audit log, one refused reuse.',
    concept:
      'Idempotency: the Idempotency-Key header lets the server recognise a retry. The first request claims the key atomically in the database; later identical requests get the stored answer; a different request with the same key is rejected.',
    tool: 'idempotency-lab',
  },
  {
    id: 'ex5',
    title: 'Import the synthetic bank statement',
    who: 'Operations (Ishita) or the owner',
    context: (f) =>
      `The bank's second statement (${num(f.record_count)} lines, source "${str(f.source_system)}") arrived as a CSV file. It is entirely synthetic.`,
    task: (f) => [
      'Download the sample statement below.',
      `On Reconciliation choose Import records, paste or load the CSV, check the column mapping and preview, then import all ${num(f.record_count)} records.`,
      'Run reconciliation once the import has finished.',
    ],
    where: [{ label: 'Reconciliation', to: '/app/reconciliation' }],
    inspect: () => [
      'The preview flags any row it cannot read before anything is sent.',
      'Importing the same file again imports nothing new - the import is idempotent too.',
    ],
    expected: () => 'All records imported; a new run whose only exception is one bank fee (exercise 6).',
    concept:
      'Reconciliation compares the bank\'s record of what happened with your own. The engine matches by the bank\'s reference and compares amounts; it never posts anything to force agreement.',
    tool: 'sample-statement',
  },
  {
    id: 'ex6',
    title: 'Investigate and record an unmatched bank fee',
    who: 'Finance (Kavya)',
    context: (f) =>
      `After the import, bank record ${str(f.fee_external_id)} (${inr(f.amount_minor)}) has no ledger transaction: the bank charged a fee nobody has recorded.`,
    task: (f) => [
      'On Reconciliation open the exception for that bank record and read the explanation.',
      `Record the fee: debit "Bank Charges", credit the operating bank account, ${inr(f.amount_minor)}.`,
      `Put ${str(f.fee_external_id)} in External reference so the engine can match it.`,
      'Run reconciliation again and confirm the record now matches.',
    ],
    where: [
      { label: 'Reconciliation', to: '/app/reconciliation' },
      { label: 'New transaction', to: '/app/transactions/new' },
    ],
    inspect: () => ['The expense is booked to Bank Charges, not Miscellaneous.', 'The bank account balance falls by the same amount.'],
    expected: () => 'A two-line transaction carrying the bank reference; the exception becomes MATCHED on the next run.',
    concept:
      'A reconciliation exception is a question, not a command. Here the answer is a missing posting - so a person records it through the normal, validated posting flow.',
  },
  {
    id: 'ex7',
    title: 'Resolve an ambiguous match using evidence',
    who: 'Operations (Ishita)',
    context: (f) =>
      `Bank credit ${str(f.external_id)} matches two live ledger receipts (${(f.candidate_references as string[] | undefined)?.join(' and ') ?? ''}). The engine refuses to guess.`,
    task: () => [
      'Open the ambiguous exception on Reconciliation and read both candidates and the suggestions.',
      'Compare the two descriptions, dates and amounts.',
      'Record a CONFIRMED_MATCH decision for the receipt that is the genuine one, with your reason.',
    ],
    where: [
      { label: 'Reconciliation', to: '/app/reconciliation' },
      { label: 'Investigations', to: '/app/investigations' },
    ],
    inspect: () => ['One entry says it was re-keyed from the bank advice.', 'The result still shows AMBIGUOUS afterwards - your decision is a recorded conclusion, not a rewrite of the engine\'s result.'],
    expected: () => 'A decision on the original receipt, with a written reason. The duplicate still needs reversing (exercise 10 shows how).',
    concept:
      'Decisions are append-only annotations with an author and a reason. The deterministic result stays as it was; the books are only changed by posting, and corrections are reversals.',
  },
  {
    id: 'ex8',
    title: 'Open an investigation and record a reasoned resolution',
    who: 'Operations (Ishita)',
    context: (f) =>
      `Ledger payment ${str(f.ledger_reference)} was recorded at one amount but the bank debited ₹5,000 less (${str(f.external_id)}). Nobody has opened a case yet.`,
    task: () => [
      'On Reconciliation open that amount-mismatch exception and choose Open investigation.',
      'In the case, add a note describing what you checked.',
      'Resolve the case with a resolution note that explains the cause and the next step.',
    ],
    where: [
      { label: 'Reconciliation', to: '/app/reconciliation' },
      { label: 'Investigations', to: '/app/investigations' },
    ],
    inspect: () => ['Notes are append-only: you cannot edit or delete them.', 'The case links back to the exception and its evidence.'],
    expected: () => 'A resolved case linked to that exception, with a resolution note of a real sentence or two.',
    concept:
      'An investigation is a workflow record, not a financial one: it tracks who looked at what and why, so the next person (or auditor) can follow the reasoning.',
  },
  {
    id: 'ex9',
    title: 'Submit a sensitive operation for approval',
    who: 'Finance (Kavya proposes, Rohan approves)',
    context: (f) =>
      `Meridian's policy: any posting of ${inr(f.threshold_minor)} or more needs a second person. Direct posting is refused above that.`,
    task: (f) => [
      `As Kavya (or the owner), try a direct posting of ${inr(f.threshold_minor)} or more and read the refusal.`,
      'Create the same transaction as a draft and submit it for approval.',
      'Try to approve it as yourself - it must be refused.',
      'Sign out, sign in as Rohan (Finance), and approve it. Check that it posted.',
    ],
    where: [
      { label: 'New transaction', to: '/app/transactions/new' },
      { label: 'Approvals', to: '/app/approvals' },
    ],
    inspect: () => ['The approval is recorded with both the maker and the checker.', 'An Operations or Auditor login cannot approve at all.'],
    expected: () => 'A posted transaction whose creator and approver are different people.',
    concept:
      'Maker-checker (four eyes): no single person can both propose and release a large payment. The rule is enforced by the server on every request, not by hiding buttons.',
  },
  {
    id: 'ex10',
    title: 'Inspect a reversal and trace its original',
    who: 'Anyone',
    context: (f) =>
      `${str(f.reversal_reference)} is a reversal. Ledger history is append-only, so mistakes are corrected by posting the opposite - never by editing.`,
    task: (f) => [
      `Open ${str(f.reversal_reference)} and read its entries.`,
      'Follow it back to the transaction it reverses.',
      'Enter the original transaction\'s reference.',
    ],
    where: [{ label: 'Transactions', to: '/app/transactions' }],
    inspect: () => [
      'Each entry of the reversal mirrors one entry of the original, on the opposite side.',
      'The original is still there, marked REVERSED.',
    ],
    expected: () => 'A reference of the form MCP-EX-nnnn - an expense that was booked to the wrong head.',
    concept:
      'Immutability: a database trigger forbids changing or deleting posted entries. A reversal is a new transaction linked to the original, so both the mistake and the correction stay on the record.',
    answer: { label: 'Original reference', placeholder: 'MCP-EX-0000' },
  },
  {
    id: 'ex11',
    title: 'Run the integrity check and understand the result',
    who: 'Anyone',
    context: () =>
      'Before a month-end close you want proof that the books are internally consistent - from the data, not from a status light.',
    task: () => [
      'Open System health and read the integrity checklist.',
      'Count how many of the checks are passing and enter the number.',
    ],
    where: [{ label: 'System health', to: '/app/system' }],
    inspect: () => [
      'Balances are re-derived from entries and compared with the cached figures.',
      'Total debits equal total credits, per currency.',
      'A failing check names what to investigate.',
    ],
    expected: () => 'Every check passing. (If one were failing, it is an incident, not a nuisance.)',
    concept:
      'A ledger earns trust by being verifiable: balances are derived, caches are checked against them, and the trial balance must net to zero.',
    answer: { label: 'Checks passing', placeholder: 'e.g. 5', inputMode: 'numeric' },
  },
  {
    id: 'ex12',
    title: 'Use the AI copilot to explain a transaction',
    who: 'Anyone with AI access',
    context: (f) =>
      `The copilot reads only this organization's records and only ever drafts text. Try it on ${str(f.transaction_reference)}.`,
    task: (f) => [
      `Open transaction ${str(f.transaction_reference)}.`,
      'Press Explain with AI and read the draft: every claim should cite something you can click.',
      'Check one claim against the ledger yourself.',
    ],
    where: [{ label: 'Transactions', to: '/app/transactions' }],
    inspect: () => ['The output is labelled as a draft needing your review.', 'It cannot post, approve, match or resolve anything.'],
    expected: () => 'An explanation with cited sources - only if live AI is configured on this installation.',
    concept:
      'AI here is read-only and grounded: it receives facts assembled by the server from your own records, and its output is validated before you see it. It is optional - everything else works without it.',
    tool: 'ai',
  },
];
