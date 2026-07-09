# Automated Transaction & Tax Compliance 

The ATICE-Audit-Engine is an SME-grade algorithmic tax compliance and double-entry auditing system. Designed for FinTech platforms, it ingests multi-format transactional data streams (legacy Point-of-Sale CSV exports and modern E-Commerce JSON API payloads), normalizes them, and batch-loads them into the Relational Ledger Vault (a highly constrained, immutable database). It programmatically validates South African VAT calculations (standard-rated, zero-rated, and exempt) and double-entry header-to-line reconciliation consistency.

---

## System Architecture Overview

```
 [Raw Multiple Data Streams]
        │ (SME POS CSVs, E-Commerce API JSONs)
        ▼
┌────────────────────────────────────────────────────────┐
│ Component 1: Multi-Format Ingestion & Norm Layer       │ <-- csv_parser.py, json_parser.py, DLQ routing
└────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────┐
│ Component 2: Relational Vault (SQLite/Postgres Vault)  │ <-- vendors, transactions, line_items (immutable)
└────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────┐
│ Component 3: The Algorithmic Auditor (Tax Matrix)       │ <-- Standard (15%), Zero (0%), Exempt, Header-to-Line check
└────────────────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────────────────┐
│ Component 4: Corporate Reporting API & Export          │ <-- Aggregated exposure, high-risk vendors, JSON output
└────────────────────────────────────────────────────────┘
        │
        ▼
 [Production Audit Package (.json)]
```

---

## Core FinTech Capabilities

### 1. Multi-Format Normalization Ingest
Accepts heterogeneous streams:
*   **Legacy POS CSV**: Ingests flat CSV logs, grouping rows dynamically by `transaction_id`.
*   **Modern API JSON**: Parses nested payloads containing transactional header arrays with child line item lists.
*   Handles diverse currency formats (e.g., `"R 1,500.00"`, `"150.00"`) and standardizes dates to `YYYY-MM-DD`.

### 2. Normalized 3-Table Relational Vault
Prevents booking redundancies:
*   `vendors`: Stores supplier profiles, 10-digit SARS VAT numbers, and dynamic compliance state.
*   `transactions`: Enforces header-level metadata constraints.
*   `line_items`: Holds itemized lists linking one-to-many to header transactions, capturing item-specific tax treatments.

### 3. Append-Only Database Immutability
Under accounting standards, financial ledgers must be immutable. Implements SQL-level `BEFORE UPDATE` and `BEFORE DELETE` triggers that block any mutations on transactions or line items, forcing correction via debit/credit reversal postings.

### 4. Structured Dead-Letter Queue (DLQ)
Payloads failing primary-key validation, date bounds (rejecting future-dated or backdated records older than 3 years), or currency conversions are routed to `logs/dead_letter.json` in JSON Lines format, preserving transaction lineage for compliance investigation.

### 5. Multi-Layer Tax Validation Matrix
Loads rules dynamically from `config/tax_rules.json`. Independently audits each line item based on its category:
*   **Standard-Rated (15% VAT)**:
    $$\text{Net Amount} = \frac{\text{Gross Amount}}{1.15}$$
    $$\text{Expected VAT} = \text{Gross Amount} - \text{Net Amount}$$
*   **Zero-Rated (0% VAT)** & **Exempt**: Expected VAT = 0.00.
Flags individual rounding variances exceeding R0.05.

### 6. Double-Entry Verification Check
Audits header-level accounting integrity:
$$\text{Transaction Gross Amount} = \sum \text{Line Items Gross Amount}$$
Any transactional imbalance triggers a high-priority double-entry discrepancy flag.

---

## Repository Blueprint

```
apex-audit-engine/
├── README.md                 <-- Comprehensive system architecture overview
├── config/
│   └── tax_rules.json        <-- Configurable tax parameters (rates, keywords)
├── database/
│   ├── schema.sql            <-- 3-table SQL DDL schema with triggers
│   └── queries.sql           <-- Pre-compiled analytical compliance queries
├── logs/
│   └── dead_letter.json      <-- Destination for unparseable raw rows (DLQ)
├── src/
│   ├── __init__.py
│   ├── db.py                 <-- Dual-database adapter (SQLite / PG fallback)
│   ├── ingestion/            <-- Component 1: Multi-format loaders
│   │   ├── __init__.py
│   │   ├── csv_parser.py
│   │   └── json_parser.py
│   ├── core/                 <-- Component 3: Tax verification loops
│   │   ├── __init__.py
│   │   └── tax_auditor.py
│   └── reporting/            <-- Component 4: Corporate report generators
│       ├── __init__.py
│       └── report_gen.py
├── run_audit.py              <-- Master integration test suite
└── audit_report.json         <-- Final production compliance package
```

---

## Execution & Verification

### Step 1: Run Master Test Suite
The master suite clears databases, creates mock streams, parses CSV and JSON data, runs compliance checks, and prints the audit terminal dashboard:
```bash
python run_audit.py
```

### Step 2: Review Output
*   **Executive Dashboard** (printed to stdout).
*   **JSON Report Export**: Review the generated [audit_report.json](audit_report.json) in the project root.
*   **Dead-Letter Queue Logs**: View isolated corrupted items in [logs/dead_letter.json](logs/dead_letter.json).

---

## Output Audit Report Schema
The corporate package exported to `audit_report.json` contains:
```json
{
  "audit_summary": {
    "audited_at": "2026-07-07T22:54:39Z",
    "total_vendors_audited": 2,
    "total_transactions_audited": 80,
    "total_line_items_audited": 204,
    "compliant_transactions": 71,
    "anomalous_transactions_count": 9,
    "total_gross_audited_zar": 90304.0,
    "total_vat_variance_detected_zar": 400.0,
    "materiality_threshold_zar": 90.3,
    "materiality_exceeded": true
  },
  "risk_analysis": {
    "high_risk_vendors": [
      {
        "vendor_name": "QuickPOS Retailers",
        "vat_registration_number": "4112233445",
        "anomalous_transactions": 5,
        "vat_variance_zar": 220.0
      },
      {
        "vendor_name": "E-Cart Distributors",
        "vat_registration_number": "4998877665",
        "anomalous_transactions": 4,
        "vat_variance_zar": 180.0
      }
    ]
  },
  "anomalies_log": {
    "vat_compliance_violations": [
      {
        "transaction_id": "POS-ANOM-1",
        "vendor_name": "QuickPOS Retailers",
        "line_item_id": 71,
        "item_description": "Premium Desk Lamp",
        "tax_category": "standard",
        "gross_amount": 1150.0,
        "declared_vat": 100.0,
        "expected_vat": 150.0,
        "vat_variance": 50.0
      }
    ],
    "double_entry_imbalances": [
      {
        "transaction_id": "POS-DE-1",
        "vendor_name": "QuickPOS Retailers",
        "transaction_gross": 1000.0,
        "line_items_gross_sum": 800.0,
        "variance": 200.0
      }
    ]
  }
}
```

License: Proprietary.
