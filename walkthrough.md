# Project Apex: Institutional-Grade Compliance Auditing Engine

This document serves as the formal architectural blueprint and system integrity report for **Project Apex**, an enterprise continuous auditing and forensic data engineering pipeline.

---

## 1. Executive Summary & Design Paradigm

Project Apex addresses a critical systemic vulnerability in corporate financial infrastructure: the manual, retroactive parsing and verification of heterogeneous transaction feeds. By automating type-safety checks, relational schema mappings, and statutory tax audits at the ingestion boundary, the engine prevents transactional leakage and financial record falsification.

### Architectural Core Principles
*   **Defensive Ingestion**: Multi-format streams are isolated and normalized before hit-loading the relational tables, ensuring format changes cannot break core schemas.
*   **Non-Repudiation**: The database vault is mathematically constrained and protected by database-level triggers to enforce append-only immutability.
*   **Vectorized Tax Verification**: Relational rows are audited using high-precision Decimal arithmetic, evaluating transaction properties dynamically against rule matrices.
*   **Materiality-Driven Governance**: Compliance anomalies are flagged and classified into rounding tolerances versus material vulnerabilities using cumulative variance ratios.

---

## 2. Comprehensive System Architecture & Flow Analysis

```
 [Raw Data Streams] ──► [Normalization Ingest] ──► [Relational Vault] ──► [Compliance Core] ──► [Audit Report]
```

### Component 1: Multi-Format Ingestion Engine & Schema Normalization (`src/ingestion/`)
The ingestion layer exposes format-specific loaders to normalize data before database writes:
*   **Procedural Flat-File Loader (`csv_parser.py`)**: Parses flat point-of-sale logs. Strips currency notations, handles locale decimal formatting variations, groups items by header transaction ID, and converts records into numeric structures.
*   **Object Graph Loader (`json_parser.py`)**: Traverses e-commerce API JSON lists. Extracts transaction header envelopes and flattens nested child arrays.
*   **Structured Dead-Letter Queue (DLQ)**: Instead of throwing unhandled runtime exceptions, malformed payloads are isolated and logged to `logs/dead_letter.json` in JSON Lines format:
    ```json
    {
      "ingested_at": "2026-07-08T10:51:22.817Z",
      "source_format": "CSV",
      "error_code": "ERR_CSV_INGEST_FAIL",
      "error_reason": "Invalid VAT registration number length or format: '123456789'",
      "raw_payloads": [...]
    }
    ```

### Component 2: Relational Ledger Vault & Security Governance (`database/schema.sql`)
The persistence layer organizes transactional records into a normalized 3-table schema:

```
   ┌────────────────┐             ┌────────────────────┐             ┌────────────────────┐
   │    VENDORS     │             │    TRANSACTIONS    │             │     LINE_ITEMS     │
   ├────────────────┤             ├────────────────────┤             ├────────────────────┤
   │ PK  vendor_id  │ ─── 1:N ──► │ PK  transaction_id │ ─── 1:N ──► │ PK  line_item_id   │
   │                │             │ FK  vendor_id      │             │ FK  transaction_id │
   └────────────────┘             └────────────────────┘             └────────────────────┘
```

To ensure strict forensic non-repudiation, the database enforces append-only constraints. Any update or delete action triggers a security violation error:
```sql
CREATE TRIGGER prevent_trans_update BEFORE UPDATE ON transactions
BEGIN
    SELECT RAISE(ABORT, 'Ledger Vault is immutable. Transaction updates are strictly forbidden.');
END;

CREATE TRIGGER prevent_trans_delete BEFORE DELETE ON transactions
BEGIN
    SELECT RAISE(ABORT, 'Ledger Vault is immutable. Transaction deletions are strictly forbidden.');
END;
```
*Note: Identical triggers are enforced on the PostgreSQL backend and SQLite test environments, shielding the audit trail against database mutations.*

### Component 3: Vectorized Audit Matrix Core (`src/core/tax_auditor.py`)
Separates operational tax rates from application logic by loading configurations dynamically from `config/tax_rules.json`. The auditor evaluates transactions against South African Revenue Service (SARS) standard-rated, zero-rated, and exempt classifications:
*   **Standard-Rate VAT (15%) Recalculation**:
    $$\text{Net Amount} = \frac{\text{Gross Amount}}{1.15}$$
    $$\text{Expected VAT} = \text{Gross Amount} - \text{Net Amount}$$
*   **Zero-Rated & Exempt VAT**:
    $$\text{Expected VAT} = 0.00$$

Every transaction undergoes a dual-layer verification loop:
1.  **Tax Variance Test**: Recalculates expected VAT and flags discrepancies exceeding a rounding tolerance of $\pm\text{R}0.05$.
2.  **Double-Entry Balance Test**: Verifies line-item sub-totals match the parent invoice total:
    $$\sum \text{line\_items.gross\_amount} == \text{transactions.gross\_amount}$$

### Component 4: Corporate Reporting Engine (`src/reporting/report_gen.py`)
Compiles flagged discrepancies and double-entry imbalances into a structured corporate report `audit_report.json` showing total exposure, high-risk vendors, and detail lists of violations.

---

## 3. Diagnostic Testing & System Integrity Report

The integration test suite executes verification assertions across multi-format loaders, trigger blocks, and audit engines, outputting a system diagnostics dashboard.

### Comprehensive Quality Assurance Output
```text
============================================================
      INTEGRATION VERIFICATION METRICS
============================================================
 PASS: CSV POS loader successfully ingested 40 transactions.
 PASS: JSON E-commerce loader successfully ingested 40 transactions.
 PASS: dead_letter.json DLQ isolated exactly 5 corrupted records.
 PASS: Identified exactly 4 Double-Entry balance anomalies.
 PASS: Identified exactly 5 VAT Compliance Violations.
 PASS: Total audited VAT Variance is R 400.00 ZAR.
============================================================
 APEX ENGINE VERIFICATION COMPLETED: ALL INTEGRATION PATHS PASS
============================================================
```

### Forensic Analysis of Integration Run Results
An audit of an initial R99,623.00 sample ledger identified a system compliance rate of **88.75%**. The auditing core flagged 9 anomalous entries generating a total tax leakage of R400.00.

The pipeline automatically applied a financial materiality threshold of 0.1% against the gross asset pool:
$$\text{Materiality Limit} = \text{R}99,623.00 \times 0.001 = \text{R}99.62$$

Because the actual variance (R400.00) exceeded the calculated materiality limit (R99.62), the engine flagged a **Systemic Compliance Breach**:

```text
============================================================
      APEX AUDITING EXECUTIVE REPORT
============================================================
 Audited At:          2026-07-08T10:51:22.928547Z
 Total Gross Audited: R 99,623.00 ZAR
 Compliance Rate:     88.75%
 Total Transactions:  80 parsed
 Compliant Trans:     71
 Anomalous Trans:     9
------------------------------------------------------------
 Total VAT Variance:  R 400.00 ZAR
 Materiality Limit:   R 99.62 ZAR (0.1%)
 WARNING: Material compliance vulnerability detected.
------------------------------------------------------------
 High-Risk Vendors:
  1. QuickPOS Retailers (VAT: 4112233445) - 5 anomalies | R 220.00 variance
  2. E-Cart Distributors (VAT: 4998877665) - 4 anomalies | R 180.00 variance
============================================================
```
