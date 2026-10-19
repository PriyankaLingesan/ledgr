import { describe, expect, it } from 'vitest';

import { formatMinor, parseAmountToMinor } from './format';

describe('amount parsing', () => {
  it('converts major units to integer minor units without float error', () => {
    expect(parseAmountToMinor('1250.50', 'USD')).toBe(125050);
    expect(parseAmountToMinor('0.1', 'USD')).toBe(10);
    expect(parseAmountToMinor('19.99', 'USD')).toBe(1999);
    expect(parseAmountToMinor('1,234.00', 'USD')).toBe(123400);
  });

  it('rejects malformed input and excess precision', () => {
    expect(parseAmountToMinor('', 'USD')).toBeNull();
    expect(parseAmountToMinor('abc', 'USD')).toBeNull();
    expect(parseAmountToMinor('1.234', 'USD')).toBeNull();
    expect(parseAmountToMinor('-5', 'USD')).toBeNull();
  });

  it('honours zero-decimal currencies', () => {
    expect(parseAmountToMinor('500', 'JPY')).toBe(500);
    expect(parseAmountToMinor('500.5', 'JPY')).toBeNull();
  });

  it('formats minor units back for display', () => {
    expect(formatMinor(125050, 'USD')).toBe('1,250.50');
    expect(formatMinor(500, 'JPY')).toBe('500');
  });
});

describe("rupee formatting", () => {
  it("groups INR the Indian way (lakh and crore) and leaves other currencies alone", () => {
    expect(formatMinor(123456789, "INR")).toBe("12,34,567.89");
    expect(formatMinor(100000000, "INR")).toBe("10,00,000.00");
    expect(formatMinor(100000000, "USD")).toBe("1,000,000.00");
  });
});
