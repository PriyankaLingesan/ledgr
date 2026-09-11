"""Money handling.

LEDGR never stores monetary values as floats. Every amount is an integer number
of *minor units* (cents, paise, yen) alongside an ISO-4217 currency code. The
currency exponent is applied only at the presentation boundary, so all ledger
arithmetic is exact by construction.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# ISO-4217 subset with minor-unit exponents. A code registry keeps the ledger
# honest about currencies without a reference table that needs seeding.
CURRENCY_EXPONENTS: dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "INR": 2,
    "CAD": 2,
    "AUD": 2,
    "SGD": 2,
    "CHF": 2,
    "JPY": 0,
    "KRW": 0,
    "KWD": 3,
    "BHD": 3,
}

CURRENCY_NAMES: dict[str, str] = {
    "USD": "US Dollar",
    "EUR": "Euro",
    "GBP": "Pound Sterling",
    "INR": "Indian Rupee",
    "CAD": "Canadian Dollar",
    "AUD": "Australian Dollar",
    "SGD": "Singapore Dollar",
    "CHF": "Swiss Franc",
    "JPY": "Japanese Yen",
    "KRW": "South Korean Won",
    "KWD": "Kuwaiti Dinar",
    "BHD": "Bahraini Dinar",
}

SUPPORTED_CURRENCIES = tuple(sorted(CURRENCY_EXPONENTS))


def is_supported(currency: str) -> bool:
    return currency in CURRENCY_EXPONENTS


def exponent(currency: str) -> int:
    try:
        return CURRENCY_EXPONENTS[currency]
    except KeyError as exc:  # pragma: no cover - guarded by request validation
        raise ValueError("unsupported currency: " + str(currency)) from exc


def to_major(amount_minor: int, currency: str) -> Decimal:
    """Render minor units as an exact decimal in major units."""
    exp = exponent(currency)
    return (Decimal(amount_minor) / (Decimal(10) ** exp)).quantize(Decimal(1).scaleb(-exp))


def to_minor(amount_major: str | Decimal, currency: str) -> int:
    """Parse a major-unit decimal into minor units.

    Rejects values carrying more precision than the currency supports rather
    than silently rounding money away.
    """
    exp = exponent(currency)
    try:
        value = Decimal(str(amount_major))
    except InvalidOperation as exc:
        raise ValueError("invalid amount: " + str(amount_major)) from exc
    scaled = value.scaleb(exp)
    if scaled != scaled.to_integral_value():
        raise ValueError("amount carries more precision than " + currency + " supports")
    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_amount(amount_minor: int, currency: str) -> str:
    """Human-readable major-unit amount, e.g. 125050 USD becomes 1250.50."""
    return format(to_major(amount_minor, currency), "f")
