# Console design

The console is an internal operations tool for people who reconcile ledgers. It is not a
marketing surface, so it has no hero sections, no gradients, no decorative motion and no
metric that isn't derived from the ledger.

The governing question for every screen is: **can an operator answer their question
without leaving it?**

---

## Information architecture

```
Dashboard              is the ledger healthy right now?
├── Accounts           what accounts exist, and what do they hold?
│   └── Account        how did this balance come to be?
├── Transactions       what has been posted?
│   ├── Transaction    exactly what happened, and does it balance?
│   └── New            post a balanced transaction
├── Ledger explorer    every entry, filterable like an auditor would
└── Audit log          who did what, under which request
```

Navigation is a fixed 228px rail — flat, no accordions. Five destinations do not need
hierarchy, and a rail that never moves means the same pixel always does the same thing.

The rail's footer carries a live system strip: API reachability, trial-balance state and
the total entry count. An operator's first question about a ledger is always "is it
consistent right now?", so the answer is permanently on screen rather than a page away.

### Navigation depth

Nothing is more than two clicks from the dashboard. Every ledger entry links to its
transaction; every entry and every transaction leg links to its account; the audit log
links to whatever it describes. Traversal follows the data model, so the UI teaches the
model as you use it.

---

## Design system

### Colour

Two neutral ramps carry almost the entire interface: `canvas → surface → surface-2 →
surface-3` for backgrounds, `border → border-strong` for structure, `ink → ink-2 → ink-3`
for text. Everything is defined as CSS custom properties and mapped into Tailwind v4
theme tokens, so light and dark are one palette with two value sets.

Colour is reserved for meaning:

| Token | Meaning | Used for |
| --- | --- | --- |
| `accent` (blue) | interactive / selected | links, primary buttons, active nav |
| `debit` (blue) | **DEBIT**, always | direction badges, debit amounts, debit column |
| `credit` (amber) | **CREDIT**, always | direction badges, credit amounts, credit column |
| `positive` (green) | a good system state | balanced, consistent, ACTIVE |
| `negative` (red) | a bad state or a negative value | errors, out-of-balance, negative balances |
| `warning` (amber-brown) | attention, not failure | REVERSED, FROZEN |

**Why blue and amber for debit and credit**, and never green and red: green/red read as
good/bad, and a debit is neither. A debit to an expense account and a debit to a customer
wallet mean opposite things commercially — the colour must encode *side*, not sentiment.
Blue and amber are distinct at a glance, distinguishable for the most common colour-vision
deficiencies, and carry no moral loading.

The pairing is defined once (`components/ledger/atoms.tsx`) and never varies: an operator
learns it on the dashboard and it still holds on the ledger explorer.

### Typography

- **Inter** for interface text, with a system-UI fallback stack.
- **IBM Plex Mono** for every number, identifier, account code and currency code, with
  `font-variant-numeric: tabular-nums`.

Monospace on all figures is not a stylistic choice. Financial tables are read by scanning
a column vertically, and digits must align to make magnitude differences visible. The same
applies to account codes and UUIDs, where recognising a shape matters more than reading it.

Scale (tight, because density is the point):

| Role | Size / weight |
| --- | --- |
| Page title | 18px / 600, tight tracking |
| Panel title | 13px / 600 |
| Body, table cells | 13px / 400 |
| Column headings, labels | 11px / 600, uppercase, 0.06em tracking |
| Metrics | 20–24px / 600, monospace |

### Spacing and structure

A 4px base unit throughout. Table rows are 36–40px; panel padding is 12–16px; page gutters
are 24px. Radii are 3–6px — a ledger row is not a card, and rounding it like one makes the
interface feel like a toy.

Structure comes from hairline 1px borders, not shadows. Panels use a single 1px border and
a nearly invisible shadow; the only real elevation in the app is on modals, which genuinely
float.

Wide tables scroll inside their own panel (`overflow-x: auto`); the page body never scrolls
horizontally.

### Density

Table rows fit ~8 columns without wrapping at 1440px, and remain readable at 1280px. Where
a cell needs two facts (account code + name, transaction reference + description), the
identifier goes on top in mono and the human-readable gloss below in muted 12px — two
lines of information in the vertical space of one and a half.

---

## Presenting debits and credits

### At entry level

A `DR`/`CR` badge in the direction colour, plus **two separate money columns** — Debit and
Credit — with a dash in the column that does not apply. This is the form accountants have
used for centuries; reproducing it means the table needs no explanation.

### At transaction level

The transaction detail page is the most important screen in the product, because it is
where an auditor decides whether to trust the system.

1. **Balance assertion strip** — total debits, a literal `=` sign, total credits, and a
   verdict panel showing `Δ 0.00`. The equality is stated, not implied. If it ever failed,
   the `=` becomes `≠` and the strip turns red.
2. **Two-column entry view** — debits on the left with a blue header rule, credits on the
   right with an amber one, each independently totalled at the foot of its own column. The
   symmetry is visible before any number is read.
3. **Full entry table** — every stored field: `seq`, position index, account, type,
   direction, amount, memo and entry id, with a totals footer.
4. **Transaction record** — status, both timestamps, actor, request id, idempotency key,
   external reference, metadata.
5. **Audit trail** — the events recorded in the same database transaction as the entries.

The `balanced` flag the page displays is recomputed by the server from the persisted
entries on every read, not echoed from the stored totals. The page proves the invariant
rather than repeating a claim about it.

### Reversal relationships

A reversal link is shown as a full-width amber strip above the transaction: *"Reversed on
&lt;time&gt; by a compensating transaction"* with a link, or *"This is a reversal of the
original transaction"* in the other direction. Both sides are always reachable in one
click, and the language is explicit that nothing was edited.

---

## The transaction builder

The screen is organised around the thing that can go wrong: an unbalanced posting.

- Each entry row carries a 2px left border in its direction colour, so the debit/credit
  split is visible as shape before any label is read.
- Direction is a segmented control, not a dropdown — two options should cost one click.
- Amounts are entered in **major units** (`1250.50`) and converted to minor units using the
  currency's exponent from the API, rejecting excess precision rather than rounding money
  away silently.
- Account options are filtered to active accounts in the selected currency and grouped by
  account type, so an invalid pick is difficult to make rather than merely reported later.
- The balance assertion strip sits below the entries and updates on every keystroke.
- A **pre-flight checks** panel lists exactly the rules the server enforces, in the server's
  own language. When the API does reject a posting, the message is already familiar.
- The **idempotency key** is visible and regenerable, with a plain explanation of what it
  does. Making the safety mechanism visible is part of teaching the domain.

---

## States

- **Empty** — a bordered icon, one sentence naming what is missing, and where useful the
  action that fills it. The ledger explorer's empty state says entries are never deleted,
  so an empty result only ever means the filters are too narrow.
- **Error** — the server's `code` in mono, its message, and the `request_id`, so an
  operator can quote something actionable rather than "it broke".
- **Loading** — skeleton rows matching the real table geometry, so the layout does not jump.
- **Stale** — React Query keeps the previous page's data visible while the next loads, so
  paginating and filtering never flash empty.

---

## Responsiveness

The rail collapses to a compact icon bar below 1024px and tables keep their columns and
scroll horizontally inside their panel. Columns are not dropped: an operator on a
split-screen laptop needs the same data, not a summarised version of it.

---

## Themes

Light and dark are both first-class; the choice is stored in `localStorage` and applied
before first paint by an inline script, so the console never flashes the wrong theme.
Dark mode is a genuine re-derivation of the palette — surfaces near-black, borders lifted,
accents desaturated and brightened — not an inverted filter.
