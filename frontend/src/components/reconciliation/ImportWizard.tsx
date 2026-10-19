/**
 * External-record import: choose a CSV (or paste one), map its columns, preview
 * the rows with per-row validation, then import.
 *
 * Nothing is sent until the preview is clean. The server validates again and
 * imports idempotently by (source, external id), so re-importing the same file
 * never duplicates a record. Importing evidence changes no ledger entry.
 */

import { UploadCloud } from 'lucide-react';
import { useMemo, useRef, useState, type ChangeEvent } from 'react';

import { useImportExternalRecords } from '../../lib/opsQueries';
import { useCurrencies } from '../../lib/queries';
import { formatMinor, parseAmountToMinor } from '../../lib/format';
import { useToast } from '../../lib/toast';
import { Modal } from '../ui/Modal';
import { Badge, Button, Field, Input, Select, Textarea } from '../ui/primitives';
import { Table, TableScroller, THead } from '../ui/Table';

type FieldKey = 'external_id' | 'amount' | 'currency' | 'direction' | 'timestamp' | 'description';

const FIELDS: { key: FieldKey; label: string; required: boolean; hints: string[] }[] = [
  { key: 'external_id', label: 'External id', required: true, hints: ['external_id', 'id', 'reference', 'ref', 'transaction_id'] },
  { key: 'amount', label: 'Amount (major units)', required: true, hints: ['amount', 'value', 'total'] },
  { key: 'currency', label: 'Currency', required: false, hints: ['currency', 'ccy', 'cur'] },
  { key: 'direction', label: 'Direction (debit/credit)', required: false, hints: ['direction', 'dr_cr', 'type', 'side'] },
  { key: 'timestamp', label: 'Timestamp', required: true, hints: ['timestamp', 'date', 'time', 'settled_at', 'created'] },
  { key: 'description', label: 'Description', required: false, hints: ['description', 'memo', 'narrative', 'details'] },
];

const MAX_ROWS = 500;

/** Minimal RFC-4180-style CSV parser: quoted fields, escaped quotes, CRLF. */
export function parseCsvText(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') {
        cell += '"';
        i += 1;
      } else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') {
      row.push(cell.trim());
      cell = '';
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i += 1;
      row.push(cell.trim());
      if (row.some((c) => c !== '')) rows.push(row);
      row = [];
      cell = '';
    } else cell += ch;
  }
  row.push(cell.trim());
  if (row.some((c) => c !== '')) rows.push(row);
  return rows;
}

interface Prepared {
  record: {
    external_id: string;
    amount_minor: number;
    currency: string;
    direction: 'DEBIT' | 'CREDIT';
    transaction_timestamp: string;
    description?: string;
  } | null;
  errors: string[];
  line: number;
}

export function ImportWizard({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast();
  const { data: currencies } = useCurrencies();
  const importRecords = useImportExternalRecords();
  const fileInput = useRef<HTMLInputElement>(null);

  const [source, setSource] = useState('');
  const [text, setText] = useState('');
  const [fileName, setFileName] = useState<string | null>(null);
  const [defaultCurrency, setDefaultCurrency] = useState('USD');
  const [mapping, setMapping] = useState<Record<FieldKey, number | ''>>({
    external_id: '',
    amount: '',
    currency: '',
    direction: '',
    timestamp: '',
    description: '',
  });

  const table = useMemo(() => parseCsvText(text), [text]);
  const hasHeader = table.length > 0 && table[0].every((c) => c === '' || Number.isNaN(Number(c.replace(/,/g, ''))));
  const headers = hasHeader ? table[0] : (table[0] ?? []).map((_, i) => `Column ${i + 1}`);
  const body = hasHeader ? table.slice(1) : table;

  const autoMap = (cols: string[]) => {
    const next: Record<FieldKey, number | ''> = { external_id: '', amount: '', currency: '', direction: '', timestamp: '', description: '' };
    for (const field of FIELDS) {
      const index = cols.findIndex((c) => field.hints.includes(c.toLowerCase().replace(/[\s-]+/g, '_')));
      if (index >= 0) next[field.key] = index;
    }
    return next;
  };

  const loadText = (value: string, name: string | null) => {
    setText(value);
    setFileName(name);
    const parsed = parseCsvText(value);
    if (parsed.length > 0) {
      const first = parsed[0];
      const header = first.every((c) => c === '' || Number.isNaN(Number(c.replace(/,/g, ''))));
      setMapping(autoMap(header ? first : []));
    }
  };

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 2_000_000) {
      toast.error('That file is too large', 'Import up to 2 MB (about 500 records) at a time.');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => loadText(String(reader.result ?? ''), file.name);
    reader.readAsText(file);
  };

  const prepared: Prepared[] = useMemo(() => {
    return body.slice(0, MAX_ROWS).map((cells, index) => {
      const errors: string[] = [];
      const get = (key: FieldKey) => (mapping[key] === '' ? '' : (cells[mapping[key] as number] ?? ''));
      const externalId = get('external_id');
      if (!externalId) errors.push('missing external id');
      const currency = (get('currency') || defaultCurrency).toUpperCase();
      if (!/^[A-Z]{3}$/.test(currency)) errors.push(`bad currency "${currency}"`);
      const minor = parseAmountToMinor(get('amount'), currency, currencies);
      if (minor === null || minor <= 0) errors.push(`bad amount "${get('amount')}"`);
      const dirRaw = (get('direction') || 'DEBIT').toUpperCase();
      const direction = dirRaw === 'CREDIT' || dirRaw === 'CR' ? 'CREDIT' : dirRaw === 'DEBIT' || dirRaw === 'DR' ? 'DEBIT' : null;
      if (!direction) errors.push(`direction "${get('direction')}" is not debit/credit`);
      const when = new Date(get('timestamp'));
      if (!get('timestamp') || Number.isNaN(when.getTime())) errors.push(`bad timestamp "${get('timestamp')}"`);
      return {
        line: index + 1 + (hasHeader ? 1 : 0),
        errors,
        record:
          errors.length === 0
            ? {
                external_id: externalId,
                amount_minor: minor as number,
                currency,
                direction: direction as 'DEBIT' | 'CREDIT',
                transaction_timestamp: when.toISOString(),
                description: get('description') || undefined,
              }
            : null,
      };
    });
  }, [body, mapping, defaultCurrency, currencies, hasHeader]);

  const invalid = prepared.filter((p) => p.errors.length > 0);
  const valid = prepared.filter((p) => p.record);
  const mappingReady = FIELDS.filter((f) => f.required).every((f) => mapping[f.key] !== '');
  const tooMany = body.length > MAX_ROWS;
  const canImport = Boolean(source.trim()) && mappingReady && valid.length > 0 && invalid.length === 0 && !tooMany;

  const reset = () => {
    setText('');
    setFileName(null);
    if (fileInput.current) fileInput.current.value = '';
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="max-w-4xl"
      title="Import external records"
      description="Choose a CSV, map its columns, review the preview, then import. Re-importing the same records never duplicates them."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={importRecords.isPending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!canImport || importRecords.isPending}
            onClick={() =>
              importRecords.mutate(
                { source_system: source.trim(), records: valid.map((p) => p.record!) },
                {
                  onSuccess: (result) => {
                    toast.success(
                      `Imported ${result.imported} record${result.imported === 1 ? '' : 's'}`,
                      result.skipped_duplicates ? `${result.skipped_duplicates} already present` : undefined,
                    );
                    reset();
                    onClose();
                  },
                  onError: (error) => toast.apiError(error),
                },
              )
            }
          >
            Import {valid.length || ''} record{valid.length === 1 ? '' : 's'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Source system" htmlFor="import-source" required hint="e.g. STRIPE_PAYOUTS, BANK_FEED_HSBC">
            <Input id="import-source" value={source} onChange={(e) => setSource(e.target.value)} />
          </Field>
          <Field label="Default currency" htmlFor="import-ccy" hint="Used when the file has no currency column">
            <Select id="import-ccy" value={defaultCurrency} onChange={(e) => setDefaultCurrency(e.target.value)}>
              {(currencies ?? [{ code: 'USD', name: 'US Dollar', exponent: 2 }]).map((c) => (
                <option key={c.code} value={c.code}>
                  {c.code}
                </option>
              ))}
            </Select>
          </Field>
        </div>

        <div>
          <div className="flex flex-wrap items-center gap-3">
            <input ref={fileInput} type="file" accept=".csv,text/csv,text/plain" onChange={onFile} className="sr-only" id="import-file" />
            <Button icon={<UploadCloud className="h-3.5 w-3.5" />} onClick={() => fileInput.current?.click()}>
              Choose CSV file
            </Button>
            <span className="text-[0.8125rem] text-ink-3">{fileName ?? 'or paste CSV below'}</span>
          </div>
          <Field label="CSV" htmlFor="import-csv" className="mt-3" hint="First row may be a header. Dates in ISO form (2026-09-08T12:00:00Z) are safest.">
            <Textarea
              id="import-csv"
              className="num min-h-28 text-xs"
              value={text}
              onChange={(e) => loadText(e.target.value, null)}
              placeholder={'external_id,amount,currency,direction,timestamp,description\nPAY-1001,1250.00,USD,debit,2026-09-08T12:00:00Z,Processor settlement'}
            />
          </Field>
        </div>

        {table.length > 0 && (
          <>
            <fieldset>
              <legend className="caption mb-2">Map columns</legend>
              <div className="grid gap-3 sm:grid-cols-3">
                {FIELDS.map((field) => (
                  <Field key={field.key} label={field.label} htmlFor={`map-${field.key}`} required={field.required}>
                    <Select
                      id={`map-${field.key}`}
                      value={mapping[field.key] === '' ? '' : String(mapping[field.key])}
                      onChange={(e) =>
                        setMapping((m) => ({ ...m, [field.key]: e.target.value === '' ? '' : Number(e.target.value) }))
                      }
                    >
                      <option value="">{field.required ? 'Choose a column…' : '(not in file)'}</option>
                      {headers.map((h, i) => (
                        <option key={i} value={i}>
                          {h || `Column ${i + 1}`}
                        </option>
                      ))}
                    </Select>
                  </Field>
                ))}
              </div>
            </fieldset>

            <div>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <p className="caption">Preview</p>
                <Badge tone={invalid.length ? 'negative' : 'positive'}>
                  {invalid.length ? `${invalid.length} row(s) need fixing` : `${valid.length} row(s) valid`}
                </Badge>
                {tooMany && <Badge tone="negative">Over {MAX_ROWS} rows - split the file</Badge>}
                {!mappingReady && <Badge tone="warning">Map the required columns</Badge>}
              </div>
              <TableScroller className="max-h-64 rounded-sm border border-border">
                <Table>
                  <THead>
                    <th className="px-3 py-2 text-xs font-medium text-ink-3">Line</th>
                    <th className="px-3 py-2 text-xs font-medium text-ink-3">External id</th>
                    <th className="px-3 py-2 text-right text-xs font-medium text-ink-3">Amount</th>
                    <th className="px-3 py-2 text-xs font-medium text-ink-3">Direction</th>
                    <th className="px-3 py-2 text-xs font-medium text-ink-3">Timestamp</th>
                    <th className="px-3 py-2 text-xs font-medium text-ink-3">Check</th>
                  </THead>
                  <tbody className="divide-y divide-border">
                    {prepared.slice(0, 50).map((p) => (
                      <tr key={p.line} className={p.errors.length ? 'bg-negative-soft/40' : undefined}>
                        <td className="num px-3 py-1.5 text-xs text-ink-3">{p.line}</td>
                        <td className="num px-3 py-1.5 text-[0.8125rem] text-ink">{p.record?.external_id ?? '—'}</td>
                        <td className="num px-3 py-1.5 text-right text-[0.8125rem] text-ink">
                          {p.record ? `${formatMinor(p.record.amount_minor, p.record.currency, currencies)} ${p.record.currency}` : '—'}
                        </td>
                        <td className="px-3 py-1.5 text-xs text-ink-2">{p.record?.direction ?? '—'}</td>
                        <td className="num px-3 py-1.5 text-xs text-ink-2">{p.record?.transaction_timestamp.slice(0, 16).replace('T', ' ') ?? '—'}</td>
                        <td className="px-3 py-1.5 text-xs">
                          {p.errors.length ? <span className="text-negative">{p.errors.join('; ')}</span> : <span className="text-positive">OK</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </TableScroller>
              {prepared.length > 50 && <p className="mt-1 text-xs text-ink-3">Showing the first 50 of {prepared.length} rows; all are validated.</p>}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
