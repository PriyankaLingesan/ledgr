"""Meridian Commerce Pvt. Ltd. - a fictional Indian B2B trading company.

This module is *pure*: it builds the whole scenario (chart of accounts, six
months of postings, the synthetic bank statements, the casework) as plain data
and touches no database. `meridian_seed` applies it through LEDGR's own services.

Determinism: nothing is random. A tiny hash-seeded generator (`Det`) makes every
"choice" (which customer, what amount, which day) a pure function of the
scenario key, so the same `as_of` date always yields byte-identical data. The
anchor date only shifts the calendar; it never changes the logical scenario.

The company buys industrial supplies (fasteners, bearings, resins, lubricants,
packaging, electricals) from suppliers on credit, sells them to business
customers on credit, collects by bank transfer, and reconciles its operating
bank account against a (synthetic) statement. Amounts are INR; GST lines are
*illustrative arithmetic in the seed*, not a tax computation - LEDGR does not
calculate tax, hold inventory quantities, or issue invoices.

Every posting here is built to be valid double entry. The builder simulates
balances as it goes and refuses (raises `ScenarioError`) to emit a posting that
would overdraw an account that forbids negative balances.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

SCENARIO_KEY = "meridian-v1"
ORG_NAME = "Meridian Commerce Pvt. Ltd."
CURRENCY = "INR"
STATEMENT_SOURCE = "DEMOBANK-CA-0142"
EMAIL_DOMAIN = "meridian-demo.test"
HISTORY_DAYS = 180
S1_FROM_DAYS, S1_TO_DAYS = 30, 74  # statement 1 covers as_of-74 .. as_of-30
S2_FROM_DAYS, S2_TO_DAYS = 1, 29  # statement 2 covers as_of-29 .. as_of-1


def inr(rupees: int) -> int:
    """Whole rupees -> paise (minor units)."""
    return int(rupees) * 100


APPROVAL_THRESHOLD_MINOR = inr(1_000_000)  # Rs 10,00,000: at/above needs a second person


class ScenarioError(AssertionError):
    """The generated scenario would break a ledger rule (a bug in this module)."""


# --------------------------------------------------------------------------
# deterministic "randomness"
# --------------------------------------------------------------------------


class Det:
    """splitmix64 seeded from a string: stable across Python versions and machines."""

    _MASK = (1 << 64) - 1

    def __init__(self, seed: str) -> None:
        self._s = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")

    def _next(self) -> int:
        self._s = (self._s + 0x9E3779B97F4A7C15) & self._MASK
        z = self._s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & self._MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & self._MASK
        return z ^ (z >> 31)

    def between(self, lo: int, hi: int) -> int:
        return lo + self._next() % (hi - lo + 1)

    def pick(self, items):
        return items[self._next() % len(items)]

    def sample_days(self, lo: int, hi: int, n: int) -> list[int]:
        chosen: set[int] = set()
        while len(chosen) < n:
            chosen.add(self.between(lo, hi))
        return sorted(chosen)


# --------------------------------------------------------------------------
# the cast
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Persona:
    key: str
    email: str
    full_name: str
    role: str  # UserRole value
    title: str
    responsibilities: str


PERSONAS: dict[str, Persona] = {
    "owner": Persona(
        "owner",
        f"meera.krishnan@{EMAIL_DOMAIN}",
        "Meera Krishnan",
        "OWNER",
        "Director & Head of Finance",
        "Owns the organization: manages users, sets the approval threshold, can do everything.",
    ),
    "maker": Persona(
        "maker",
        f"kavya.menon@{EMAIL_DOMAIN}",
        "Kavya Menon",
        "FINANCE",
        "Accounts Executive (maker)",
        "Records transactions and proposes large ones for approval. Cannot approve her own.",
    ),
    "checker": Persona(
        "checker",
        f"rohan.iyer@{EMAIL_DOMAIN}",
        "Rohan Iyer",
        "FINANCE",
        "Finance Manager (checker)",
        "Approves other people's drafts above the threshold. Cannot approve his own.",
    ),
    "ops": Persona(
        "ops",
        f"ishita.rao@{EMAIL_DOMAIN}",
        "Ishita Rao",
        "OPERATIONS",
        "Reconciliation & Operations Analyst",
        "Runs reconciliation, records match decisions, works investigation cases. Cannot post or approve.",
    ),
    "auditor": Persona(
        "auditor",
        f"nikhil.shah@{EMAIL_DOMAIN}",
        "Nikhil Shah",
        "AUDITOR",
        "Internal Auditor",
        "Read-only access to everything, including the audit log. Cannot change anything.",
    ),
}

# (key, name, city, what they buy)
CUSTOMERS = [
    (
        "sundaram",
        "Sundaram Hardware & Tools Pvt Ltd",
        "Chennai",
        "hex-bolt and anchor fastener sets",
    ),
    ("kaveri", "Kaveri Agro Implements LLP", "Mysuru", "bearings and lubricant drums"),
    ("bharat", "Bharat Mobility Components Ltd", "Pune", "ball-bearing assemblies"),
    ("ganga", "Ganga Packaging Industries", "Kolkata", "polymer resin granules"),
    ("lakshmi", "Lakshmi Textile Mills Pvt Ltd", "Coimbatore", "machine lubricants and spares"),
    ("himalaya", "Himalaya Pharma Distributors", "Dehradun", "packaging film and cartons"),
    ("rajputana", "Rajputana Cement Traders", "Jaipur", "industrial fasteners and bags"),
    ("konkan", "Konkan Marine Supplies", "Mangaluru", "marine-grade fasteners"),
    ("deccan", "Deccan Electricals Pvt Ltd", "Hyderabad", "cables, switchgear and fittings"),
    ("narmada", "Narmada Steel Fabricators", "Vadodara", "heavy-duty bearings and bolts"),
    ("punjab", "Punjab Agro Foods Ltd", "Ludhiana", "food-grade packaging materials"),
    ("malabar", "Malabar Spice Exports", "Kochi", "export packaging and pallets"),
]
_CUSTOMER = {c[0]: c for c in CUSTOMERS}

SUPPLIERS = [
    ("apex", "Apex Industrial Fasteners Pvt Ltd", "Ludhiana", "fasteners"),
    ("orient", "Orient Polymers & Resins", "Vapi", "resins"),
    ("shakti", "Shakti Bearings Co", "Rajkot", "bearings"),
    ("neelkanth", "Neelkanth Electricals Wholesale", "Delhi", "electricals"),
    ("coastal", "Coastal Packaging Materials", "Surat", "packaging"),
    ("zenith", "Zenith Lubricants Ltd", "Mumbai", "lubricants"),
    ("vindhya", "Vindhya Tools & Dies", "Indore", "tooling"),
]
_SUPPLIER = {s[0]: s for s in SUPPLIERS}

# (code, name, type, allows_negative_balance, description)
CHART_OF_ACCOUNTS: list[tuple[str, str, str, bool, str]] = [
    ("CASH.PETTY", "Petty Cash", "ASSET", False, "Cash in the office safe."),
    (
        "BANK.DNB.OPS",
        "Demo National Bank - Operating Current A/c ..0142",
        "ASSET",
        False,
        "The account whose (synthetic) statement is reconciled in this demo.",
    ),
    (
        "BANK.DCB.COLL",
        "Demo Commercial Bank - Collections A/c ..7731",
        "ASSET",
        False,
        "Cheque and small-transfer collections, swept to the operating account. Not reconciled in this demo.",
    ),
    (
        "AR.TRADE",
        "Accounts Receivable - Trade Debtors",
        "ASSET",
        True,
        "Control account: customer names are in transaction descriptions (no sub-ledger).",
    ),
    (
        "INV.STOCK",
        "Inventory - Trading Stock",
        "ASSET",
        False,
        "Stock at cost. LEDGR holds values only - no quantities.",
    ),
    ("GST.INPUT", "GST Input Credit (illustrative)", "ASSET", False, "GST paid on purchases."),
    (
        "TDS.RECEIVABLE",
        "TDS Receivable (deducted by customers)",
        "ASSET",
        False,
        "Tax a customer withheld from a payment.",
    ),
    (
        "AP.TRADE",
        "Accounts Payable - Suppliers",
        "LIABILITY",
        True,
        "Control account for supplier bills.",
    ),
    (
        "GST.OUTPUT",
        "GST Output Payable (illustrative)",
        "LIABILITY",
        True,
        "GST charged on sales, owed to the government.",
    ),
    ("LOAN.WC", "Working Capital Loan", "LIABILITY", True, "Bank working-capital facility."),
    ("EQ.SHARE", "Share Capital", "EQUITY", True, "Capital contributed by the owners."),
    ("EQ.RETAINED", "Retained Earnings (opening)", "EQUITY", True, "Profit brought forward."),
    ("REV.SALES", "Sales - Trading Goods", "REVENUE", True, "Sales before GST."),
    (
        "REV.OTHER",
        "Other Income - Bank Interest",
        "REVENUE",
        True,
        "Interest credited by the bank.",
    ),
    ("EXP.COGS", "Cost of Goods Sold", "EXPENSE", True, "Periodic costing journal."),
    (
        "EXP.RETURNS",
        "Sales Returns & Refunds",
        "EXPENSE",
        True,
        "Contra-revenue, shown as an expense.",
    ),
    ("EXP.RENT", "Warehouse Rent", "EXPENSE", True, "Warehouse lease."),
    ("EXP.SALARY", "Salaries & Wages", "EXPENSE", True, "Payroll for 14 staff."),
    ("EXP.UTILITIES", "Utilities (electricity, telecom)", "EXPENSE", True, "Power and broadband."),
    ("EXP.LOGISTICS", "Freight & Logistics", "EXPENSE", True, "Third-party transport."),
    ("EXP.BANKFEES", "Bank Charges", "EXPENSE", True, "Fees charged by the bank."),
    ("EXP.INTEREST", "Interest on Working Capital Loan", "EXPENSE", True, "Monthly loan interest."),
    ("EXP.MISC", "Office & Miscellaneous", "EXPENSE", True, "Small office costs."),
]
_NO_NEGATIVE = {code for code, _n, _t, allow, _d in CHART_OF_ACCOUNTS if not allow}
_ASSET_LIKE = {code for code, _n, t, _a, _d in CHART_OF_ACCOUNTS if t in ("ASSET", "EXPENSE")}

# --------------------------------------------------------------------------
# data classes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    account: str
    side: str  # "D" | "C"
    amount: int  # paise
    memo: str | None = None


@dataclass
class Posting:
    ref: str
    day: date
    at: datetime
    description: str
    legs: list[Leg]
    actor: str  # persona key
    external_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    mode: str = "direct"  # direct | draft (maker->checker) | pending | rejected
    story: str | None = None
    note: str | None = None  # decision note for rejected drafts

    @property
    def total(self) -> int:
        return sum(leg.amount for leg in self.legs if leg.side == "D")


@dataclass
class Reversal:
    ref: str
    original_ref: str
    day: date
    at: datetime
    reason: str
    actor: str
    story: str | None = None


@dataclass
class BankRecord:
    external_id: str
    at: datetime
    amount: int
    direction: str  # CREDIT = money in, DEBIT = money out (bank's point of view)
    description: str
    statement: str  # "S1" | "S2"
    story: str | None = None


@dataclass
class DecisionSpec:
    result: tuple[str, str]  # ("external", bank id) | ("internal", ledger ref)
    decision: str  # CONFIRMED_MATCH | NOT_A_MATCH
    internal_ref: str | None
    reason: str
    actor: str


@dataclass
class CaseSpec:
    key: str
    title: str
    summary: str
    priority: str
    status: str  # OPEN | RESOLVED
    result: tuple[str, str] | None
    account: str | None
    notes: list[tuple[str, str]]  # (persona, text)
    creator: str = "ops"
    resolution_note: str | None = None
    correction_ref: str | None = None
    source_reference: str | None = None
    source: str = "RECONCILIATION"
    window_days: tuple[int, int] | None = None  # (from_k, to_k) days before as_of


@dataclass
class Scenario:
    as_of: date
    opening: Posting
    pre: list[Posting | Reversal]  # before the first reconciliation run
    casework: list[Posting | Reversal]  # corrections made while working the run's exceptions
    post: list[Posting | Reversal]  # after the run (the most recent weeks)
    bank_records: list[BankRecord]
    decisions: list[DecisionSpec]
    cases: list[CaseSpec]
    facts: dict[str, Any]
    balances: dict[str, int]  # final net (debit-positive) paise by account code

    @property
    def postings(self) -> list[Posting | Reversal]:
        return [self.opening, *self.pre, *self.casework, *self.post]

    def s1(self) -> list[BankRecord]:
        return [r for r in self.bank_records if r.statement == "S1"]

    def s2(self) -> list[BankRecord]:
        return [r for r in self.bank_records if r.statement == "S2"]


# --------------------------------------------------------------------------
# builder
# --------------------------------------------------------------------------


def _fmt(paise: int) -> str:
    """Indian digit grouping, e.g. 1180000 rupees -> '11,80,000'."""
    rupees, p = divmod(paise, 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join([*parts, tail])
    return f"Rs {s}" + (f".{p:02d}" if p else "")


class _Builder:
    def __init__(self, as_of: date) -> None:
        self.as_of = as_of
        self.start = as_of - timedelta(days=HISTORY_DAYS - 1)
        self.r = Det(SCENARIO_KEY)
        self.net: dict[str, int] = dict.fromkeys((c[0] for c in CHART_OF_ACCOUNTS), 0)
        self.counters: dict[str, int] = {}
        self.bank_seq = 41000
        self.pre: list[Posting | Reversal] = []
        self.casework: list[Posting | Reversal] = []
        self.late: list[Posting | Reversal] = []
        self.bank_records: list[BankRecord] = []
        self.by_ref: dict[str, Posting] = {}
        self.facts: dict[str, Any] = {}
        self.ar_queue: list[dict[str, Any]] = []
        self.ap_queue: list[dict[str, Any]] = []
        self.sales_base_in_period = 0
        self.output_gst_in_period = 0
        self.customer_cycle = 0
        self.supplier_cycle = 0
        self.last_electricity: Posting | None = None
        self.decisions: list[DecisionSpec] = []
        self.cases: list[CaseSpec] = []
        self._sink = self.pre  # where emitted events go

    # ---- helpers -------------------------------------------------------
    def day_of_k(self, k: int) -> date:
        return self.as_of - timedelta(days=k)

    def k_of(self, d: date) -> int:
        return (self.as_of - d).days

    def window(self, d: date) -> str | None:
        k = self.k_of(d)
        if S1_FROM_DAYS <= k <= S1_TO_DAYS:
            return "S1"
        if S2_FROM_DAYS <= k <= S2_TO_DAYS:
            return "S2"
        return None

    def ref(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"MCP-{prefix}-{self.counters[prefix]:04d}"

    def moment(self, d: date, hour: int | None = None) -> datetime:
        h = hour if hour is not None else self.r.between(4, 11)
        return datetime(d.year, d.month, d.day, h, self.r.between(0, 59), tzinfo=UTC)

    def bank_id(self, channel: str, d: date) -> str:
        self.bank_seq += self.r.between(3, 47)
        return f"DNB{channel}{d:%y%m%d}{self.bank_seq:05d}"

    def _apply(self, legs: list[Leg], sign: int = 1) -> None:
        for leg in legs:
            self.net[leg.account] += sign * (leg.amount if leg.side == "D" else -leg.amount)
        for leg in legs:
            if leg.account in _NO_NEGATIVE and self.net[leg.account] < 0:
                raise ScenarioError(f"{leg.account} would go negative ({self.net[leg.account]})")

    def emit(self, item: Posting | Reversal) -> None:
        self._sink.append(item)

    def post(
        self,
        prefix: str,
        d: date,
        description: str,
        legs: list[Leg],
        actor: str,
        *,
        mode: str | None = None,
        story: str | None = None,
        channel: str | None = None,
        party: str | None = None,
        bank_amount: int | None = None,
        bank_id: str | None = None,
        bank_visible: bool = True,
        explicit_ref: str | None = None,
        external_reference: str | None = None,
        hour: int | None = None,
        **meta: Any,
    ) -> Posting:
        debits = sum(leg.amount for leg in legs if leg.side == "D")
        credits = sum(leg.amount for leg in legs if leg.side == "C")
        if debits != credits or debits <= 0:
            raise ScenarioError(f"unbalanced posting {description}: {debits} vs {credits}")
        if mode is None:
            mode = "draft" if debits >= APPROVAL_THRESHOLD_MINOR else "direct"
        ref = explicit_ref or self.ref(prefix)
        at = self.moment(d, hour)

        ext = external_reference
        if channel is not None and ext is None:
            bank_leg = next((leg for leg in legs if leg.account == "BANK.DNB.OPS"), None)
            window = self.window(d)
            if bank_leg is not None and len(legs) == 2 and window is not None:
                ext = bank_id or self.bank_id(channel, d)
                if bank_visible:
                    money_in = bank_leg.side == "D"
                    amount = bank_amount if bank_amount is not None else bank_leg.amount
                    self.bank_records.append(
                        BankRecord(
                            external_id=ext if bank_id is None else bank_id,
                            at=at + timedelta(minutes=self.r.between(1, 25)),
                            amount=amount,
                            direction="CREDIT" if money_in else "DEBIT",
                            description=_narration(channel, money_in, party, ref, description),
                            statement=window,
                            story=story,
                        )
                    )
        posting = Posting(
            ref=ref,
            day=d,
            at=at,
            description=description,
            legs=legs,
            actor=actor,
            external_reference=ext,
            metadata={
                "simulated": True,
                "scenario": SCENARIO_KEY,
                **({"story": story} if story else {}),
                **meta,
            },
            mode=mode,
            story=story,
        )
        if mode in ("direct", "draft"):
            self._apply(legs)
        self.by_ref[ref] = posting
        self.emit(posting)
        return posting

    def reverse(
        self, original: Posting, d: date, reason: str, actor: str, story: str | None = None
    ) -> Reversal:
        rv = Reversal(
            ref=self.ref("RV"),
            original_ref=original.ref,
            day=d,
            at=self.moment(d),
            reason=reason,
            actor=actor,
            story=story,
        )
        self._apply(original.legs, sign=-1)
        self.emit(rv)
        return rv

    # ---- builders for the standard flows --------------------------------
    def sales_invoice(
        self, d: date, customer: str, base_rupees: int, *, queue: bool, story: str | None = None
    ) -> Posting:
        _k, name, city, goods = _CUSTOMER[customer]
        base = inr(base_rupees)
        gst = base * 18 // 100
        gross = base + gst
        p = self.post(
            "SI",
            d,
            f"Sales invoice - {name} ({city}) - {goods}, GST @18% (illustrative)",
            [
                Leg("AR.TRADE", "D", gross, f"Invoice to {name}"),
                Leg("REV.SALES", "C", base),
                Leg("GST.OUTPUT", "C", gst, "GST @18% (illustrative)"),
            ],
            "maker",
            story=story,
            customer=name,
            base_minor=base,
            gst_minor=gst,
        )
        self.sales_base_in_period += base
        self.output_gst_in_period += gst
        if queue:
            self.ar_queue.append(
                {
                    "ref": p.ref,
                    "customer": customer,
                    "amount": gross,
                    "due": self.day_index(d) + self.r.between(20, 45),
                }
            )
        return p

    def purchase_bill(
        self, d: date, supplier: str, base_rupees: int, *, queue: bool, story: str | None = None
    ) -> Posting:
        _k, name, city, goods = _SUPPLIER[supplier]
        base = inr(base_rupees)
        gst = base * 18 // 100
        gross = base + gst
        p = self.post(
            "PB",
            d,
            f"Purchase bill - {name} ({city}) - {goods} stock, GST @18% (illustrative)",
            [
                Leg("INV.STOCK", "D", base, f"Stock from {name}"),
                Leg("GST.INPUT", "D", gst, "GST input credit (illustrative)"),
                Leg("AP.TRADE", "C", gross, f"Bill from {name}"),
            ],
            "maker",
            story=story,
            supplier=name,
            base_minor=base,
            gst_minor=gst,
        )
        if queue:
            self.ap_queue.append(
                {
                    "ref": p.ref,
                    "supplier": supplier,
                    "amount": gross,
                    "due": self.day_index(d) + self.r.between(18, 34),
                }
            )
        return p

    def receipt(
        self,
        d: date,
        customer: str,
        invoice_ref: str,
        amount: int,
        *,
        bank: str | None = None,
        **kw: Any,
    ) -> Posting:
        _k, name, _c, _g = _CUSTOMER[customer]
        target = bank or ("BANK.DNB.OPS" if amount >= inr(350_000) else "BANK.DCB.COLL")
        channel = ("R" if amount >= inr(200_000) else "N") if target == "BANK.DNB.OPS" else None
        via = {"R": "RTGS", "N": "NEFT"}.get(channel or "", "cheque / UPI collection")
        return self.post(
            "RC",
            d,
            f"Customer receipt - {name} against {invoice_ref} via {via}",
            [Leg(target, "D", amount), Leg("AR.TRADE", "C", amount, f"Settles {invoice_ref}")],
            "maker",
            channel=channel,
            party=name,
            customer=name,
            invoice=invoice_ref,
            **kw,
        )

    def supplier_payment(
        self,
        d: date,
        supplier: str,
        bill_ref: str,
        amount: int,
        *,
        channel: str | None = None,
        **kw: Any,
    ) -> Posting:
        _k, name, _c, _g = _SUPPLIER[supplier]
        channel = channel or ("R" if amount >= inr(200_000) else "N")
        via = {"R": "RTGS", "N": "NEFT", "C": "cheque"}[channel]
        return self.post(
            "PP",
            d,
            f"Supplier payment - {name} against {bill_ref} via {via}",
            [Leg("AP.TRADE", "D", amount, f"Settles {bill_ref}"), Leg("BANK.DNB.OPS", "C", amount)],
            "maker",
            channel=channel,
            party=name,
            supplier=name,
            bill=bill_ref,
            **kw,
        )

    def expense(
        self,
        d: date,
        account: str,
        description: str,
        amount: int,
        *,
        channel: str | None,
        party: str | None = None,
        **kw: Any,
    ) -> Posting:
        return self.post(
            "EX",
            d,
            description,
            [Leg(account, "D", amount), Leg("BANK.DNB.OPS", "C", amount)],
            "maker",
            channel=channel,
            party=party,
            **kw,
        )

    def day_index(self, d: date) -> int:
        return (d - self.start).days

    # ---- ensuring the operating account can cover a payment ------------------
    def fund_operating(self, d: date, needed: int) -> None:
        short = needed - self.net["BANK.DNB.OPS"]
        if short <= 0:
            return
        amount = ((short + inr(100_000) - 1) // inr(100_000)) * inr(100_000)
        if self.net["BANK.DCB.COLL"] < amount:
            raise ScenarioError(f"cannot fund operating account on {d}: need {needed}")
        self.sweep(d, amount)

    def sweep(self, d: date, amount: int) -> Posting:
        return self.post(
            "TR",
            d,
            "Internal transfer - sweep from Collections A/c to Operating A/c (IMPS)",
            [Leg("BANK.DNB.OPS", "D", amount), Leg("BANK.DCB.COLL", "C", amount)],
            "maker",
            channel="I",
            party="OWN ACCOUNT",
        )

    # ---- the six months -----------------------------------------------------------
    def run(self) -> None:
        self._opening()
        schedule = self._schedule()
        for i in range(HISTORY_DAYS):
            d = self.start + timedelta(days=i)
            k = self.k_of(d)
            if k == S2_TO_DAYS:  # first day of statement 2: switch to the post-run phase
                self._sink = self.casework
                self._casework_events()
                self._sink = self.late
            dom = i % 30
            period = i // 30
            if dom == 0:
                self.sales_base_in_period = 0
                self.output_gst_in_period = 0
                self._prev_output_balance = -self.net["GST.OUTPUT"]
            self._regular_day(d, i, dom, period, schedule[period])
            self._story_day(d, k)

    def _schedule(self) -> list[dict[str, Any]]:
        plans = []
        for _p in range(HISTORY_DAYS // 30):
            plans.append(
                {
                    "sales": self.r.sample_days(1, 27, 9),
                    "purchases": self.r.sample_days(2, 25, 4),
                    "logistics": self.r.sample_days(3, 26, 3),
                }
            )
        return plans

    def _opening(self) -> None:
        d = self.start
        self._opening_ar = [
            ("sundaram", 1_770_000),
            ("kaveri", 1_416_000),
            ("bharat", 1_298_000),
            ("ganga", 1_121_000),
            ("deccan", 826_000),
            ("malabar", 669_000),
        ]
        self._opening_ap = [
            ("apex", 1_062_000),
            ("orient", 826_000),
            ("shakti", 708_000),
            ("coastal", 649_000),
            ("zenith", 555_000),
        ]
        legs = [
            Leg("BANK.DNB.OPS", "D", inr(4_800_000), "Opening bank balance"),
            Leg("BANK.DCB.COLL", "D", inr(1_200_000)),
            Leg("CASH.PETTY", "D", inr(25_000)),
            Leg("AR.TRADE", "D", inr(7_100_000), "Six open customer invoices"),
            Leg("INV.STOCK", "D", inr(9_500_000), "Stock on hand at cost"),
            Leg("LOAN.WC", "C", inr(8_000_000)),
            Leg("AP.TRADE", "C", inr(3_800_000), "Five open supplier bills"),
            Leg("GST.OUTPUT", "C", inr(600_000)),
            Leg("EQ.SHARE", "C", inr(5_000_000)),
            Leg("EQ.RETAINED", "C", inr(5_225_000)),
        ]
        self.opening = self.post(
            "OB",
            d,
            "Opening balances brought forward (illustrative opening position)",
            legs,
            "owner",
            mode="direct",
            hour=4,
            story="opening",
        )
        self.pre.remove(self.opening)  # kept separately on the Scenario
        for n, (cust, amount) in enumerate(self._opening_ar, 1):
            self.ar_queue.append(
                {
                    "ref": f"OPENING-INV-{n}",
                    "customer": cust,
                    "amount": inr(amount),
                    "due": self.r.between(2, 28),
                }
            )
        for n, (sup, amount) in enumerate(self._opening_ap, 1):
            self.ap_queue.append(
                {
                    "ref": f"OPENING-BILL-{n}",
                    "supplier": sup,
                    "amount": inr(amount),
                    "due": self.r.between(3, 24),
                }
            )

    def _regular_day(self, d: date, i: int, dom: int, period: int, plan: dict[str, Any]) -> None:
        mon = f"{d:%b %Y}"
        if dom == 0:
            self.expense(
                d,
                "EXP.RENT",
                f"Warehouse rent - {mon} (Sai Ram Realty Holdings, Peenya warehouse)",
                inr(120_000),
                channel="R",
                party="SAI RAM REALTY HOLDINGS",
            )
        if dom in plan["sales"]:
            customer = CUSTOMERS[self.customer_cycle % len(CUSTOMERS)][0]
            self.customer_cycle += 1
            base = self.r.between(150, 840) * 1000
            self.sales_invoice(d, customer, base, queue=True)
        if dom in plan["purchases"]:
            supplier = SUPPLIERS[self.supplier_cycle % len(SUPPLIERS)][0]
            self.supplier_cycle += 1
            base = self.r.between(400, 840) * 1000
            self.purchase_bill(d, supplier, base, queue=True)
        if dom == 5:
            self.fund_operating(d, inr(15_000))
            self.post(
                "TR",
                d,
                "Petty cash top-up - cheque encashed",
                [Leg("CASH.PETTY", "D", inr(15_000)), Leg("BANK.DNB.OPS", "C", inr(15_000))],
                "maker",
                channel="C",
                party="SELF CHEQUE",
            )
        if dom == 8 or dom == 17 or dom == 25:
            spare = self.net["BANK.DCB.COLL"] - inr(100_000)
            if spare >= inr(300_000):
                self.sweep(d, (spare // inr(1_000)) * inr(1_000))
        if dom == 9:
            amt = inr(self.r.between(38, 52) * 1000)
            self.fund_operating(d, amt)
            p = self.expense(
                d,
                "EXP.UTILITIES",
                f"Electricity charges - warehouse - {mon}",
                amt,
                channel="D",
                party="STATE ELECTRICITY BOARD",
                story="electricity" if period == 5 else None,
            )
            if period == 5:
                self.last_electricity = p
        if dom == 14:
            amt = inr(11_800)
            self.fund_operating(d, amt)
            self.expense(
                d,
                "EXP.UTILITIES",
                f"Broadband and telecom - {mon}",
                amt,
                channel="D",
                party="BROADBAND PROVIDER",
            )
        if dom == 15:
            amt = inr(self.r.between(1_500, 2_400))
            self.post(
                "RC",
                d,
                f"Interest credited by the bank - {mon}",
                [Leg("BANK.DNB.OPS", "D", amt), Leg("REV.OTHER", "C", amt)],
                "maker",
                channel="I",
                party="BANK INTEREST",
            )
        if dom in plan["logistics"]:
            amt = inr(self.r.between(55, 130) * 1000)
            self.fund_operating(d, amt)
            self.expense(
                d,
                "EXP.LOGISTICS",
                f"Freight - SwiftHaul Carriers - consignment batch {d:%d%b}",
                amt,
                channel="N",
                party="SWIFTHAUL CARRIERS",
            )
        if dom == 18:
            amt = inr(self.r.between(9_300, 14_500))
            if self.net["CASH.PETTY"] >= amt:
                self.post(
                    "EX",
                    d,
                    f"Office and miscellaneous - petty cash - {mon}",
                    [Leg("EXP.MISC", "D", amt), Leg("CASH.PETTY", "C", amt)],
                    "maker",
                )
        if dom == 20:
            self._gst_settlement(d)
        if dom == 26:
            amt = inr(self.r.between(68, 72) * 1000)
            self.fund_operating(d, amt)
            self.expense(
                d,
                "EXP.INTEREST",
                f"Working-capital loan interest - {mon}",
                amt,
                channel="D",
                party="LOAN INTEREST",
            )
        if dom == 27:
            amt = inr(self.r.between(920, 960) * 1000)
            self.fund_operating(d, amt)
            self.expense(
                d,
                "EXP.SALARY",
                f"Salaries - {mon} - 14 employees (bulk transfer)",
                amt,
                channel="S",
                party="PAYROLL FILE",
            )
        if dom == 28:
            self._month_end(d, period, mon)
        self._receipts(d, i)
        self._supplier_payments(d, i)

    def _month_end(self, d: date, period: int, mon: str) -> None:
        k = self.k_of(d)
        cogs = (self.sales_base_in_period * 72 // 100) // 10000 * 10000
        cogs = min(cogs, self.net["INV.STOCK"] - inr(500_000))
        if cogs > 0:
            self.post(
                "JV",
                d,
                f"Cost of goods sold - {mon} (periodic inventory costing journal)",
                [Leg("EXP.COGS", "D", cogs), Leg("INV.STOCK", "C", cogs)],
                "checker",
            )
        # Regular bank charge, except where a story supplies its own (so the story is unambiguous).
        if abs(k - 48) > 6 and abs(k - 2) > 6:
            self.fund_operating(d, inr(590))
            self.expense(
                d,
                "EXP.BANKFEES",
                f"Bank charges - {mon} (account maintenance, NEFT/RTGS, incl. GST)",
                inr(590),
                channel="F",
                party="BANK CHARGES",
            )

    def _gst_settlement(self, d: date) -> None:
        payable = max(0, self._prev_output_balance)
        offset = min(self.net["GST.INPUT"], payable)
        cash = payable - offset
        if offset > 0:
            self.post(
                "JV",
                d,
                "GST - set off input credit against output payable (illustrative)",
                [Leg("GST.OUTPUT", "D", offset), Leg("GST.INPUT", "C", offset)],
                "checker",
            )
        if cash > 0:
            self.fund_operating(d, cash)
            self.post(
                "PP",
                d,
                "GST payment to the government - net output payable (illustrative)",
                [Leg("GST.OUTPUT", "D", cash), Leg("BANK.DNB.OPS", "C", cash)],
                "checker",
                channel="R",
                party="GOVT GST PAYMENT",
            )

    def _receipts(self, d: date, i: int) -> None:
        due = sorted(
            (x for x in self.ar_queue if x["due"] <= i), key=lambda x: (x["due"], x["ref"])
        )
        for item in due[:2]:
            self.ar_queue.remove(item)
            self.receipt(d, item["customer"], item["ref"], item["amount"])

    def _supplier_payments(self, d: date, i: int) -> None:
        due = sorted(
            (x for x in self.ap_queue if x["due"] <= i), key=lambda x: (x["due"], x["ref"])
        )
        for item in due[:1]:
            if self.net["BANK.DNB.OPS"] - item["amount"] < inr(1_500_000):
                continue  # keep a reserve for payroll, GST and rent
            self.ap_queue.remove(item)
            self.supplier_payment(d, item["supplier"], item["ref"], item["amount"])

    # ---- the stories -----------------------------------------------------------------------
    def _story_day(self, d: date, k: int) -> None:
        handler = getattr(self, f"_story_k{k}", None)
        if handler is not None:
            handler(d)

    # Invoices and bills that the exception stories will settle.
    def _story_k130(self, d: date) -> None:
        p = self.sales_invoice(
            d, "sundaram", 250_000, queue=False, story="receipt_sundaram_invoice"
        )
        self.facts["sundaram_invoice"] = p.ref

    def _story_k105(self, d: date) -> None:
        inv = self.facts["sundaram_invoice"]
        p = self.receipt(
            d, "sundaram", inv, inr(295_000), bank="BANK.DNB.OPS", story="receipt_sundaram"
        )
        self.facts["receipt_sundaram"] = p.ref

    def _story_k120(self, d: date) -> None:  # a material sale: needs maker-checker
        p = self.sales_invoice(d, "rajputana", 1_450_000, queue=True, story="material_sale")
        self.facts["material_sale"] = p.ref

    def _story_k100(self, d: date) -> None:  # a refund
        self.post(
            "RF",
            d,
            "Refund to Sundaram Hardware & Tools Pvt Ltd - returned damaged fasteners (credit note CN-118)",
            [
                Leg("EXP.RETURNS", "D", inr(42_000)),
                Leg("GST.OUTPUT", "D", inr(7_560), "GST on the returned goods (illustrative)"),
                Leg("BANK.DNB.OPS", "C", inr(49_560)),
            ],
            "maker",
            story="refund",
        )

    def _story_k110(self, d: date) -> None:  # a mis-coded posting, reversed and reposted
        amt = inr(41_400)
        wrong = self.post(
            "EX",
            d,
            f"Electricity charges - warehouse - {d:%b %Y} (booked to Office & Misc by mistake)",
            [Leg("EXP.MISC", "D", amt), Leg("BANK.DNB.OPS", "C", amt)],
            "maker",
            story="miscoded",
        )
        rv = self.reverse(
            wrong,
            d,
            "Wrong expense head - reposted to Utilities",
            "maker",
            story="miscoded_reversal",
        )
        self.post(
            "EX",
            d,
            f"Electricity charges - warehouse - {d:%b %Y}",
            [Leg("EXP.UTILITIES", "D", amt), Leg("BANK.DNB.OPS", "C", amt)],
            "maker",
            story="miscoded_repost",
        )
        self.facts["reversal_demo_ref"] = rv.ref
        self.facts["reversal_demo_original"] = wrong.ref

    def _story_k96(self, d: date) -> None:
        p = self.sales_invoice(d, "kaveri", 500_000, queue=False, story="tds_invoice")
        self.facts["tds_invoice"] = p.ref

    def _story_k88(self, d: date) -> None:
        p = self.sales_invoice(d, "deccan", 300_000, queue=False, story="refmis_invoice")
        self.facts["refmis_invoice"] = p.ref

    def _story_k84(self, d: date) -> None:
        p = self.sales_invoice(d, "narmada", 400_000, queue=False, story="notcredited_invoice")
        self.facts["notcredited_invoice"] = p.ref

    def _story_k77(self, d: date) -> None:
        p = self.purchase_bill(d, "orient", 270_000, queue=False, story="discount_bill")
        self.facts["discount_bill"] = p.ref

    def _story_k75(self, d: date) -> None:
        p = self.purchase_bill(d, "shakti", 200_000, queue=False, story="dupext_bill")
        self.facts["dupext_bill"] = p.ref

    def _story_k70(self, d: date) -> None:
        p = self.sales_invoice(d, "lakshmi", 90_000, queue=False, story="ambig_invoice")
        self.facts["ambig_invoice"] = p.ref
        z = self.purchase_bill(d, "zenith", 100_000, queue=False, story="timing_bill")
        self.facts["timing_bill"] = z.ref

    def _story_k60(self, d: date) -> None:
        p = self.sales_invoice(d, "konkan", 190_000, queue=False, story="unapplied_invoice")
        self.facts["unapplied_invoice"] = p.ref

    def _story_k40(self, d: date) -> None:  # big supplier bill (material), paid later via a draft
        p = self.purchase_bill(d, "apex", 1_000_000, queue=False, story="apex_big_bill")
        self.facts["apex_big_bill"] = p.ref

    # Statement-1 exceptions.
    def _story_k52(self, d: date) -> None:  # TDS short payment: bank credited Rs 10,000 less
        inv = self.facts["tds_invoice"]
        p = self.receipt(
            d,
            "kaveri",
            inv,
            inr(590_000),
            bank="BANK.DNB.OPS",
            story="tds_receipt",
            bank_amount=inr(580_000),
        )
        self.facts["tds_receipt"] = p.ref
        self.facts["tds_ext"] = p.external_reference

    def _story_k48(self, d: date) -> None:  # a bank fee nobody has recorded
        bid = self.bank_id("F", d)
        self.facts["fee_s1_ext"] = bid
        self.bank_records.append(
            BankRecord(
                bid,
                self.moment(d),
                inr(1_180),
                "DEBIT",
                "BANK CHARGES - QUARTERLY SERVICING, SMS ALERTS AND CHEQUE-BOOK CHARGES INCL GST",
                "S1",
                "fee_s1",
            )
        )

    def _story_k45(self, d: date) -> None:  # reference typed wrongly in the ledger
        inv = self.facts["refmis_invoice"]
        right = self.bank_id("N", d)
        wrong = right[:-2] + right[-1] + right[-2]  # last two digits transposed
        if wrong == right:
            wrong = right[:-1] + ("0" if right[-1] != "0" else "1")
        p = self.receipt(
            d,
            "deccan",
            inv,
            inr(354_000),
            bank="BANK.DNB.OPS",
            story="refmis_receipt",
            bank_id=wrong,
        )
        # `bank_id=wrong` made the *ledger* carry `wrong` and the bank record carry `wrong`; swap
        # the bank side to the true id so the two disagree, as they would after a typing slip.
        for rec in self.bank_records:
            if rec.external_id == wrong and rec.story == "refmis_receipt":
                rec.external_id = right
        self.facts["refmis_receipt"] = p.ref
        self.facts["refmis_ledger_ext"] = wrong
        self.facts["refmis_bank_ext"] = right

    def _story_k41(self, d: date) -> None:  # the bank debited a payment twice (different bank ids)
        bill = self.facts["dupext_bill"]
        p = self.supplier_payment(
            d, "shakti", bill, inr(236_000), channel="R", story="dupext_payment"
        )
        first = p.external_reference
        twin = first[:-2] + f"{(int(first[-2:]) + 1) % 100:02d}"
        self.bank_records.append(
            BankRecord(
                twin,
                self.moment(d, 11),
                inr(236_000),
                "DEBIT",
                _narration("R", False, "SHAKTI BEARINGS CO", bill, "re-presented"),
                "S1",
                "dupext_twin",
            )
        )
        self.facts["dupext_payment"] = p.ref
        self.facts["dupext_ext"] = first
        self.facts["dupext_twin_ext"] = twin

    def _story_k39(
        self, d: date
    ) -> None:  # a receipt entered twice in the ledger (same bank reference)
        inv = self.facts["ambig_invoice"]
        p = self.receipt(
            d, "lakshmi", inv, inr(106_200), bank="BANK.DNB.OPS", story="ambig_original"
        )
        self.facts["ambig_original"] = p.ref
        self.facts["ambig_ext"] = p.external_reference

    def _story_k38(self, d: date) -> None:
        ext = self.facts["ambig_ext"]
        dup = self.post(
            "RC",
            d,
            "Customer receipt - Lakshmi Textile Mills Pvt Ltd - re-keyed from bank advice (duplicate entry)",
            [
                Leg("BANK.DNB.OPS", "D", inr(106_200)),
                Leg("AR.TRADE", "C", inr(106_200), "Duplicate credit to the invoice"),
            ],
            "maker",
            external_reference=ext,
            story="ambig_duplicate",
        )
        self.facts["ambig_duplicate"] = dup.ref
        # An unidentified credit from a customer whose invoice is still open.
        bid = self.bank_id("N", d)
        self.facts["unapplied_ext"] = bid
        self.bank_records.append(
            BankRecord(
                bid,
                self.moment(d, 9),
                inr(224_200),
                "CREDIT",
                "NEFT CR-KONKAN MARINE SUPPLIES-NO INVOICE REFERENCE GIVEN",
                "S1",
                "unapplied",
            )
        )

    def _story_k36(self, d: date) -> None:  # recorded in the ledger, but the bank never credited it
        inv = self.facts["notcredited_invoice"]
        p = self.receipt(
            d,
            "narmada",
            inv,
            inr(472_000),
            bank="BANK.DNB.OPS",
            story="notcredited_receipt",
            bank_visible=False,
        )
        self.facts["notcredited_receipt"] = p.ref
        self.facts["notcredited_ext"] = p.external_reference

    def _story_k33(self, d: date) -> None:
        bill = self.facts["discount_bill"]
        self.fund_operating(d, inr(318_600))
        p = self.supplier_payment(
            d,
            "orient",
            bill,
            inr(318_600),
            channel="R",
            story="discount_payment",
            bank_amount=inr(313_600),
        )
        self.facts["discount_payment"] = p.ref
        self.facts["discount_ext"] = p.external_reference
        # A cheque that has not been presented yet: a valid timing difference.
        zbill = self.facts["timing_bill"]
        self.fund_operating(d, inr(118_000))
        chq = self.post(
            "PP",
            d,
            f"Supplier payment - Zenith Lubricants Ltd against {zbill} via cheque 004512",
            [Leg("AP.TRADE", "D", inr(118_000)), Leg("BANK.DNB.OPS", "C", inr(118_000))],
            "maker",
            external_reference="CHQ-004512",
            story="timing_cheque",
        )
        self.facts["timing_cheque"] = chq.ref

    def _story_k28(self, d: date) -> None:  # ...and it clears on statement 2
        self.bank_records.append(
            BankRecord(
                "CHQ-004512",
                self.moment(d, 7),
                inr(118_000),
                "DEBIT",
                "CHQ PAID 004512 - ZENITH LUBRICANTS LTD",
                "S2",
                "timing_clears",
            )
        )

    def _story_k19(self, d: date) -> None:  # the electricity bill is entered a second time...
        orig = self.last_electricity
        if orig is None:
            raise ScenarioError("electricity story needs the period's electricity posting")
        dup = self.post(
            "EX",
            d,
            orig.description + " (entered again by mistake)",
            [Leg("EXP.UTILITIES", "D", orig.total), Leg("BANK.DNB.OPS", "C", orig.total)],
            "maker",
            external_reference=orig.external_reference,
            story="electricity_dup",
        )
        self.facts["electricity_original"] = orig.ref
        self.facts["electricity_duplicate"] = dup.ref

    def _story_k18(self, d: date) -> None:  # ...and reversed the next day, with no explanation
        dup = self.by_ref[self.facts["electricity_duplicate"]]
        rv = self.reverse(dup, d, "Duplicate entry", "maker", story="electricity_reversal")
        self.facts["electricity_reversal"] = rv.ref

    def _story_k5(self, d: date) -> None:  # a draft the checker declined
        p = self.post(
            "PB",
            d,
            "Purchase bill - Neelkanth Electricals Wholesale (Delhi) - switchgear stock (no GST invoice attached)",
            [Leg("INV.STOCK", "D", inr(1_400_000)), Leg("AP.TRADE", "C", inr(1_400_000))],
            "maker",
            mode="rejected",
            story="rejected_draft",
        )
        p.note = "Supplier's GST invoice is not attached. Do not book the bill until it is - resubmit with the invoice."
        self.facts["rejected_draft_description"] = p.description

    def _story_k2(
        self, d: date
    ) -> None:  # statement 2's unrecorded bank fee (the learner's exercise)
        bid = self.bank_id("F", d)
        self.facts["fee_s2_ext"] = bid
        self.bank_records.append(
            BankRecord(
                bid,
                self.moment(d, 9),
                inr(590),
                "DEBIT",
                "BANK CHARGES - NEFT/RTGS TRANSACTION CHARGES AND ACCOUNT MAINTENANCE INCL GST",
                "S2",
                "fee_s2",
            )
        )

    def _story_k1(self, d: date) -> None:  # a large supplier payment waiting for a second person
        bill = self.facts["apex_big_bill"]
        self.post(
            "PP",
            d,
            f"Supplier payment - Apex Industrial Fasteners Pvt Ltd against {bill} via RTGS",
            [
                Leg("AP.TRADE", "D", inr(1_180_000), f"Settles {bill}"),
                Leg("BANK.DNB.OPS", "C", inr(1_180_000)),
            ],
            "maker",
            mode="pending",
            story="pending_payment",
        )
        self.facts["pending_payment_bill"] = bill

    # ---- corrections made while working statement 1's exceptions ------------------------------------
    def _casework_events(self) -> None:
        d_fee = self.day_of_k(47)
        self.fund_operating(d_fee, inr(1_180))
        fee = self.post(
            "EX",
            d_fee,
            "Bank charges - quarterly servicing, SMS alerts and cheque-book (recorded from the bank statement)",
            [Leg("EXP.BANKFEES", "D", inr(1_180)), Leg("BANK.DNB.OPS", "C", inr(1_180))],
            "maker",
            external_reference=self.facts["fee_s1_ext"],
            story="fee_s1_fix",
            explicit_ref=self.ref("EX"),
        )
        self.facts["fee_s1_fix"] = fee.ref

        d_tds = self.day_of_k(50)
        original = self.by_ref[self.facts["tds_receipt"]]
        rv = self.reverse(
            original,
            d_tds,
            "Customer deducted 2% TDS: bank credited Rs 5,80,000, not Rs 5,90,000. Re-recorded below.",
            "checker",
            story="tds_reversal",
        )
        fixed = self.post(
            "RC",
            d_tds,
            "Customer receipt - Kaveri Agro Implements LLP against "
            f"{self.facts['tds_invoice']} via RTGS (corrected: net of TDS)",
            [Leg("BANK.DNB.OPS", "D", inr(580_000)), Leg("AR.TRADE", "C", inr(580_000))],
            "checker",
            external_reference=self.facts["tds_ext"],
            story="tds_receipt_fixed",
        )
        tds = self.post(
            "JV",
            d_tds,
            "TDS deducted by Kaveri Agro Implements LLP (2% of the Rs 5,00,000 taxable value) - moved from debtors",
            [Leg("TDS.RECEIVABLE", "D", inr(10_000)), Leg("AR.TRADE", "C", inr(10_000))],
            "checker",
            story="tds_journal",
        )
        self.facts["tds_reversal"] = rv.ref
        self.facts["tds_receipt_fixed"] = fixed.ref
        self.facts["tds_journal"] = tds.ref

        self.decisions.append(
            DecisionSpec(
                result=("external", self.facts["refmis_bank_ext"]),
                decision="CONFIRMED_MATCH",
                internal_ref=self.facts["refmis_receipt"],
                reason=(
                    "Same customer, amount (Rs 3,54,000) and date. The ledger reference "
                    f"{self.facts['refmis_ledger_ext']} has the last two digits of the bank's "
                    f"{self.facts['refmis_bank_ext']} swapped; Deccan's remittance advice confirms the bank id."
                ),
                actor="ops",
            )
        )

    # ---- cases (applied by the seeder once the run exists) -----------------------------------------
    def build_cases(self) -> None:
        f = self.facts
        self.cases = [
            CaseSpec(
                "fee_s1",
                "Bank charges on the statement are missing from the ledger",
                f"The bank debited Rs 1,180 ({f['fee_s1_ext']}) for quarterly servicing and cheque-book charges. "
                "No ledger transaction carries that reference, so the books understate expenses by the same amount.",
                "LOW",
                "RESOLVED",
                ("external", f["fee_s1_ext"]),
                "EXP.BANKFEES",
                [
                    (
                        "ops",
                        "Checked the statement narration: quarterly servicing + cheque-book + SMS alerts. Genuine bank fee.",
                    ),
                    (
                        "checker",
                        f"Recorded as {f['fee_s1_fix']} with the bank reference. Next action: none - a new reconciliation run will show it as matched.",
                    ),
                ],
                resolution_note=f"Fee recorded in the ledger as {f['fee_s1_fix']} (Dr Bank Charges, Cr Bank) carrying the bank's reference.",
                correction_ref=f["fee_s1_fix"],
                source_reference="MCP-CASE-01",
            ),
            CaseSpec(
                "tds",
                "Customer receipt short by Rs 10,000 (TDS)",
                f"Receipt {f['tds_receipt']} from Kaveri Agro Implements LLP was recorded at Rs 5,90,000 but the bank credited "
                "Rs 5,80,000. Difference: Rs 10,000.",
                "MEDIUM",
                "RESOLVED",
                ("external", f["tds_ext"]),
                "AR.TRADE",
                [
                    ("ops", "Asked the customer for their remittance advice."),
                    (
                        "ops",
                        "Remittance advice received: 2% TDS on the Rs 5,00,000 taxable value was withheld (Rs 10,000). Evidence attached to the customer file.",
                    ),
                    (
                        "checker",
                        f"Reversed {f['tds_reversal']}, re-recorded the receipt at the amount actually received and moved the TDS to TDS Receivable ({f['tds_journal']}).",
                    ),
                ],
                resolution_note="Customer withheld Rs 10,000 TDS. Receipt re-recorded net; TDS moved to TDS Receivable via journal.",
                correction_ref=f["tds_receipt_fixed"],
                source_reference="MCP-CASE-02",
            ),
            CaseSpec(
                "refmis",
                "Payment reference differs between ledger and bank",
                f"Bank credit {f['refmis_bank_ext']} (Rs 3,54,000) has no ledger transaction, and ledger receipt "
                f"{f['refmis_receipt']} (Rs 3,54,000) names {f['refmis_ledger_ext']}, which the bank never reported.",
                "MEDIUM",
                "RESOLVED",
                ("external", f["refmis_bank_ext"]),
                "BANK.DNB.OPS",
                [
                    (
                        "ops",
                        "Amount, customer and date agree; the references differ by two transposed digits. Suggestion engine proposes the receipt.",
                    ),
                    (
                        "ops",
                        "Confirmed with Deccan's remittance advice. Recorded a CONFIRMED_MATCH decision with the reason.",
                    ),
                ],
                resolution_note="Typing slip in the ledger reference; match confirmed against the customer's remittance advice. No posting needed.",
                source_reference="MCP-CASE-03",
            ),
            CaseSpec(
                "dupext",
                "Bank appears to have debited a supplier payment twice",
                f"One ledger payment ({f['dupext_payment']}, Rs 2,36,000 to Shakti Bearings Co) but two identical bank debits: "
                f"{f['dupext_ext']} and {f['dupext_twin_ext']}, minutes apart.",
                "HIGH",
                "OPEN",
                ("external", f["dupext_twin_ext"]),
                "BANK.DNB.OPS",
                [
                    (
                        "ops",
                        "Both debits are on the statement with the same amount and narration. Only one payment was instructed.",
                    ),
                    (
                        "ops",
                        "Next action: raise a query with the bank for a reversal of the second debit and ask Shakti Bearings whether they received two credits.",
                    ),
                ],
                source_reference="MCP-CASE-04",
            ),
            CaseSpec(
                "notcredited",
                "Receipt recorded in the ledger but not on the bank statement",
                f"{f['notcredited_receipt']} records Rs 4,72,000 from Narmada Steel Fabricators ({f['notcredited_ext']}), "
                "but no matching credit appears on the statement.",
                "HIGH",
                "OPEN",
                ("internal", f["notcredited_receipt"]),
                "BANK.DNB.OPS",
                [
                    (
                        "ops",
                        "Not a timing difference: the date is well inside the statement period.",
                    ),
                    (
                        "ops",
                        "Next action: ask Narmada Steel for the UTR of the payment. If none exists the receipt was recorded too early and should be reversed.",
                    ),
                ],
                source_reference="MCP-CASE-05",
            ),
            CaseSpec(
                "unapplied",
                "Bank credit with no ledger entry and no invoice reference",
                f"Credit {f['unapplied_ext']} for Rs 2,24,200 from Konkan Marine Supplies has no matching ledger transaction. "
                f"Konkan's invoice {f['unapplied_invoice']} (same amount) is still open.",
                "MEDIUM",
                "OPEN",
                ("external", f["unapplied_ext"]),
                "AR.TRADE",
                [
                    (
                        "ops",
                        "Amount equals Konkan's open invoice. The suggestion list will show that invoice - but an invoice is not a receipt.",
                    ),
                    (
                        "ops",
                        "Next action: confirm with Konkan, then record a receipt (Dr Bank, Cr Receivables) carrying the bank reference, rather than 'matching' it to the invoice.",
                    ),
                ],
                source_reference="MCP-CASE-06",
            ),
            CaseSpec(
                "ambiguous",
                "Same bank reference on two ledger receipts",
                f"Bank credit {f['ambig_ext']} (Rs 1,06,200, Lakshmi Textile Mills) matches two live ledger receipts: "
                f"{f['ambig_original']} and {f['ambig_duplicate']}. The engine will not guess.",
                "MEDIUM",
                "OPEN",
                ("external", f["ambig_ext"]),
                "BANK.DNB.OPS",
                [
                    (
                        "ops",
                        f"{f['ambig_duplicate']} is described as re-keyed from the bank advice - it looks like a duplicate entry of {f['ambig_original']}.",
                    ),
                    (
                        "ops",
                        "Next action: confirm the original as the match, then reverse the duplicate so the ledger shows one receipt.",
                    ),
                ],
                source_reference="MCP-CASE-07",
            ),
            CaseSpec(
                "electricity_reversal",
                "Reversal of a duplicated electricity bill needs review",
                f"{f['electricity_duplicate']} duplicated {f['electricity_original']}; it was reversed by {f['electricity_reversal']} "
                "with only the reason 'Duplicate entry'.",
                "LOW",
                "OPEN",
                None,
                "EXP.UTILITIES",
                [
                    (
                        "auditor",
                        "Reversal looks reasonable, but the reviewer should confirm the bank shows a single debit for the electricity bill.",
                    ),
                    (
                        "ops",
                        "Next action: after the next reconciliation run, check the electricity bill matches exactly one bank debit.",
                    ),
                ],
                creator="auditor",
                source="MANUAL",
                source_reference="MCP-CASE-08",
            ),
        ]

    def finish(self) -> Scenario:
        self.build_cases()
        return Scenario(
            as_of=self.as_of,
            opening=self.opening,
            pre=self.pre,
            casework=self.casework,
            post=self.late,
            bank_records=sorted(self.bank_records, key=lambda r: (r.at, r.external_id)),
            decisions=self.decisions,
            cases=self.cases,
            facts=self.facts,
            balances=dict(self.net),
        )


def _narration(channel: str, money_in: bool, party: str | None, ref: str, description: str) -> str:
    mode = {
        "N": "NEFT",
        "R": "RTGS",
        "I": "IMPS",
        "C": "CHQ",
        "F": "FEE",
        "D": "ECS",
        "S": "NEFT",
    }.get(channel, "TRF")
    who = (party or "").upper()
    if channel == "F":
        return "BANK CHARGES - ACCOUNT MAINTENANCE AND TRANSACTION CHARGES INCL GST"
    if channel == "I" and who == "BANK INTEREST":
        return "INT.PD - CREDIT INTEREST ON AVERAGE BALANCE"
    arrow = "CR" if money_in else "DR"
    return f"{mode} {arrow}-{who}-{ref}"[:160]


def build_scenario(as_of: date) -> Scenario:
    builder = _Builder(as_of)
    builder.run()
    return builder.finish()


def statement_csv(records: list[BankRecord]) -> str:
    """The synthetic bank statement in the column layout the console's import wizard maps."""
    lines = ["external_id,amount,currency,direction,timestamp,description"]
    for r in sorted(records, key=lambda x: (x.at, x.external_id)):
        amount = f"{r.amount // 100}.{r.amount % 100:02d}"
        desc = r.description.replace('"', "'")
        lines.append(
            f'{r.external_id},{amount},{CURRENCY},{r.direction.lower()},{r.at:%Y-%m-%dT%H:%M:%SZ},"{desc}"'
        )
    return "\n".join(lines) + "\n"
