"""Canonical financial schema and label → metric mapping."""
from __future__ import annotations

import re

PNL_METRICS = [
    "revenue", "other_income", "material_cost", "employee_cost",
    "other_operating_expenses", "ebitda", "depreciation", "ebit",
    "finance_cost", "pbt", "tax", "pat",
]

BS_METRICS = [
    "share_capital", "reserves", "net_worth", "fixed_assets", "investments",
    "inventory", "receivables", "cash", "other_current_assets", "total_assets",
    "long_term_borrowings", "short_term_borrowings", "trade_payables",
    "other_liabilities", "total_liabilities",
]

CF_METRICS = ["cfo", "cfi", "cff", "capex", "opening_cash", "closing_cash"]

ALL_METRICS = PNL_METRICS + BS_METRICS + CF_METRICS

STATEMENT_OF: dict[str, str] = (
    {m: "pnl" for m in PNL_METRICS}
    | {m: "balance_sheet" for m in BS_METRICS}
    | {m: "cash_flow" for m in CF_METRICS}
)

METRIC_LABELS: dict[str, str] = {
    "revenue": "Revenue from Operations",
    "other_income": "Other Income",
    "material_cost": "Cost of Materials Consumed",
    "employee_cost": "Employee Benefit Expenses",
    "other_operating_expenses": "Other Operating Expenses",
    "ebitda": "EBITDA",
    "depreciation": "Depreciation & Amortisation",
    "ebit": "EBIT",
    "finance_cost": "Finance Costs",
    "pbt": "Profit Before Tax",
    "tax": "Tax Expense",
    "pat": "Profit After Tax",
    "share_capital": "Share Capital",
    "reserves": "Reserves & Surplus",
    "net_worth": "Net Worth",
    "fixed_assets": "Fixed Assets (Net Block)",
    "investments": "Investments",
    "inventory": "Inventories",
    "receivables": "Trade Receivables",
    "cash": "Cash & Cash Equivalents",
    "other_current_assets": "Other Current Assets",
    "total_assets": "Total Assets",
    "long_term_borrowings": "Long-term Borrowings",
    "short_term_borrowings": "Short-term Borrowings",
    "trade_payables": "Trade Payables",
    "other_liabilities": "Other Liabilities & Provisions",
    "total_liabilities": "Total Liabilities",
    "cfo": "Cash Flow from Operations",
    "cfi": "Cash Flow from Investing",
    "cff": "Cash Flow from Financing",
    "capex": "Capital Expenditure",
    "opening_cash": "Opening Cash Balance",
    "closing_cash": "Closing Cash Balance",
}

# ordered patterns — first match wins; keep more specific patterns first
_LABEL_PATTERNS: list[tuple[str, str]] = [
    (r"revenue\s+from\s+operations?|operating\s+revenue|net\s+sales|\bsales\b|\bturnover\b|^revenue$", "revenue"),
    (r"other\s+income", "other_income"),
    (r"cost\s+of\s+(materials?|goods)|material\s+cost|purchases?\s+of\s+stock|raw\s+material", "material_cost"),
    (r"employee\s+(benefit|cost)|staff\s+cost|salaries|personnel", "employee_cost"),
    (r"other\s+(operating\s+)?expenses?|administrative|selling\s+and\s+distribution", "other_operating_expenses"),
    (r"\bebitda\b", "ebitda"),
    (r"depreciation|amorti[sz]ation", "depreciation"),
    (r"\bebit\b(?!da)", "ebit"),
    (r"finance\s+cost|interest\s+expense|borrowing\s+cost", "finance_cost"),
    (r"profit\s+before\s+tax|\bpbt\b", "pbt"),
    (r"tax\s+expense|current\s+tax|total\s+tax|^tax$", "tax"),
    (r"profit\s+after\s+tax|\bpat\b|profit\s+for\s+the\s+(year|period)|net\s+profit", "pat"),
    # capex must precede fixed_assets: "Purchase of Fixed Assets" is capex
    (r"capital\s+expenditure|purchase\s+of\s+(fixed\s+assets|property)|\bcapex\b", "capex"),
    (r"share\s+capital|equity\s+share", "share_capital"),
    (r"reserves|other\s+equity|surplus", "reserves"),
    (r"net\s*worth|total\s+equity|shareholders?.{0,3}\s*funds?", "net_worth"),
    (r"fixed\s+assets|property,?\s*plant|net\s+block|tangible\s+assets", "fixed_assets"),
    (r"^investments?$|non.current\s+investments?|current\s+investments?", "investments"),
    (r"inventor(y|ies)|stock.in.trade", "inventory"),
    (r"trade\s+receivables?|sundry\s+debtors|accounts?\s+receivable", "receivables"),
    # opening/closing balances must precede the generic "cash" pattern below, which
    # would otherwise swallow "Cash and cash equivalents at the beginning of the year"
    (r"opening\s+(cash|balance)|cash.{0,40}at\s+the\s+beginning", "opening_cash"),
    (r"closing\s+(cash|balance)|cash.{0,40}at\s+the\s+end", "closing_cash"),
    (r"cash\s+(and|&)\s+(cash\s+equivalents|bank)|cash\s+equivalents|bank\s+balances", "cash"),
    (r"other\s+current\s+assets|short.term\s+loans\s+and\s+advances", "other_current_assets"),
    (r"total\s+assets", "total_assets"),
    (r"long.term\s+borrowings?|term\s+loans?", "long_term_borrowings"),
    (r"short.term\s+borrowings?|working\s+capital\s+loan|cash\s+credit", "short_term_borrowings"),
    (r"trade\s+payables?|sundry\s+creditors|accounts?\s+payable", "trade_payables"),
    (r"other\s+(current\s+)?liabilit|provisions", "other_liabilities"),
    (r"total\s+liabilit", "total_liabilities"),
    # Net cash from/(used in) <activity> — the reported totals. The wording between
    # "net cash" and the activity varies wildly ("from", "used in", "generated from",
    # "from / (used in)"), so allow a short gap rather than enumerating variants.
    # Deliberately does NOT match "Cash generated from operations", which is the
    # PRE-TAX subtotal above "Income taxes paid" — CFO is the figure after tax.
    (r"net\s+cash\b.{0,40}?operating\s+activit"
     r"|cash\s+flows?\s+from\s+operating\s+activit|\bcfo\b", "cfo"),
    (r"net\s+cash\b.{0,40}?investing\s+activit"
     r"|cash\s+flows?\s+from\s+investing\s+activit|\bcfi\b", "cfi"),
    (r"net\s+cash\b.{0,40}?financing\s+activit"
     r"|cash\s+flows?\s+from\s+financing\s+activit|\bcff\b", "cff"),
]


# Cash-flow *movement* lines. These name the same nouns as balance-sheet metrics
# ("...in cash and cash equivalents", "...in short-term borrowings") but state the
# change over the year, not the closing balance, so mapping them to a balance metric
# silently substitutes a delta for a stock figure.
_MOVEMENT_LINE = re.compile(r"^\W*(net\s+)?(increase|decrease)\b")


def map_label_to_metric(label: str) -> str | None:
    """Map a raw statement label to a canonical metric name, or None."""
    t = re.sub(r"\s+", " ", label.strip().lower())
    if _MOVEMENT_LINE.search(t):
        return None
    for pattern, metric in _LABEL_PATTERNS:
        if re.search(pattern, t):
            return metric
    return None


STATEMENT_KEYWORDS = {
    "balance_sheet": ["balance sheet", "statement of assets and liabilities", "equity and liabilities"],
    "profit_and_loss": ["profit and loss", "profit & loss", "statement of income", "income statement", "revenue from operations"],
    "cash_flow": ["cash flow", "cash-flow", "statement of cash flows"],
    "notes": ["notes to accounts", "notes forming part", "significant accounting policies"],
}


def detect_statement_type(page_text: str) -> tuple[str, int]:
    """Score page text and return (statement_type, score)."""
    t = page_text.lower()
    best, best_score = "other", 0
    for stype, kws in STATEMENT_KEYWORDS.items():
        score = sum(3 for kw in kws if kw in t)
        # secondary signals
        if stype == "profit_and_loss":
            score += sum(1 for kw in ["total revenue", "employee benefit", "finance costs", "tax expense"] if kw in t)
        if stype == "balance_sheet":
            score += sum(1 for kw in ["share capital", "trade receivables", "total assets", "borrowings"] if kw in t)
        if stype == "cash_flow":
            score += sum(1 for kw in ["operating activities", "investing activities", "financing activities"] if kw in t)
        if score > best_score:
            best, best_score = stype, score
    return best, best_score
