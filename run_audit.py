import os
import sys
import json
import csv
import random
import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.db import DatabaseConnection
from src.ingestion.csv_parser import parse_csv_file
from src.ingestion.json_parser import parse_json_file
from src.core.tax_auditor import AlgorithmicAuditor
from src.reporting.report_gen import generate_corporate_report

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("run_audit")


def round_decimal(val):
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def generate_mock_inputs(project_root):
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)

    csv_path = os.path.join(data_dir, "raw_pos.csv")
    json_path = os.path.join(data_dir, "raw_ecommerce.json")

    current_date = datetime.now()
    base_date = current_date - timedelta(days=120)

    # -------------------------------------------------------------------------
    # 1. Generate Mock POS CSV
    # Flat format: transaction header columns duplicated on each line item row
    # -------------------------------------------------------------------------
    # Let's generate 40 CSV transactions:
    # - 35 compliant
    # - 3 with VAT anomalies
    # - 2 with Double-Entry anomalies (Header Gross != sum of line item Gross)
    # - We will also insert 3 corrupted rows (e.g. invalid date, invalid VAT, empty transaction_id)
    # -------------------------------------------------------------------------
    csv_rows = []

    # Compliant POS transactions
    for i in range(35):
        tx_id = f"POS-{1000 + i}"
        tx_date = (base_date + timedelta(days=random.randint(0, 100))).strftime(
            "%Y-%m-%d"
        )
        vendor_name = "QuickPOS Retailers"
        vendor_vat = "4112233445"
        pm = random.choice(["Cash", "Debit Card"])

        # 2 line items: 1 standard, 1 zero_rated (foodstuff)
        item1_gross = Decimal(random.randint(100, 1000))
        item1_vat = round_decimal(
            item1_gross - round_decimal(item1_gross / Decimal("1.15"))
        )

        item2_gross = Decimal(random.randint(20, 200))
        item2_vat = Decimal("0.00")  # Zero-rated

        gross_total = item1_gross + item2_gross

        # Line 1
        csv_rows.append(
            {
                "transaction_id": tx_id,
                "transaction_date": tx_date,
                "vendor_name": vendor_name,
                "vendor_vat": vendor_vat,
                "gross_amount": str(gross_total),
                "payment_method": pm,
                "item_description": "Office Stationeries",
                "quantity": "2",
                "unit_price": str(round(item1_gross / 2, 2)),
                "line_gross_amount": str(item1_gross),
                "tax_category": "standard",
                "declared_vat": str(item1_vat),
            }
        )
        # Line 2
        csv_rows.append(
            {
                "transaction_id": tx_id,
                "transaction_date": tx_date,
                "vendor_name": vendor_name,
                "vendor_vat": vendor_vat,
                "gross_amount": str(gross_total),
                "payment_method": pm,
                "item_description": "Fresh Apples",
                "quantity": "5",
                "unit_price": str(round(item2_gross / 5, 2)),
                "line_gross_amount": str(item2_gross),
                "tax_category": "zero_rated",
                "declared_vat": str(item2_vat),
            }
        )

    # POS VAT Anomalies (Standard rated with wrong declared vat)
    # Anomaly 1: Gross=1150 (expected VAT = 150), declared VAT = 100 (variance = 50)
    # Anomaly 2: Gross=1150 (expected VAT = 150), declared VAT = 200 (variance = 50)
    # Anomaly 3: Exempt item ("Rent fee") but has declared VAT R120 (should be 0, variance = 120)
    pos_anoms = [
        # Anomaly 1
        {
            "id": "POS-ANOM-1",
            "gross": "1150.00",
            "desc": "Premium Desk Lamp",
            "qty": "1",
            "price": "1150.00",
            "cat": "standard",
            "declared": "100.00",
            "date": (base_date + timedelta(days=10)).strftime("%Y-%m-%d"),
        },
        # Anomaly 2
        {
            "id": "POS-ANOM-2",
            "gross": "1150.00",
            "desc": "Wireless Mouse",
            "qty": "1",
            "price": "1150.00",
            "cat": "standard",
            "declared": "200.00",
            "date": (base_date + timedelta(days=20)).strftime("%Y-%m-%d"),
        },
        # Anomaly 3
        {
            "id": "POS-ANOM-3",
            "gross": "5000.00",
            "desc": "Office rent fee",
            "qty": "1",
            "price": "5000.00",
            "cat": "exempt",
            "declared": "120.00",
            "date": (base_date + timedelta(days=30)).strftime("%Y-%m-%d"),
        },
    ]
    for an in pos_anoms:
        csv_rows.append(
            {
                "transaction_id": an["id"],
                "transaction_date": an["date"],
                "vendor_name": "QuickPOS Retailers",
                "vendor_vat": "4112233445",
                "gross_amount": an["gross"],
                "payment_method": "Debit Card",
                "item_description": an["desc"],
                "quantity": an["qty"],
                "unit_price": an["price"],
                "line_gross_amount": an["gross"],
                "tax_category": an["cat"],
                "declared_vat": an["declared"],
            }
        )

    # POS Double-Entry Anomalies (Header Gross != sum of line item gross)
    # Anomaly 4: Header Gross = 1000, Line Gross Sum = 800 (variance = 200)
    # Anomaly 5: Header Gross = 500, Line Gross Sum = 600 (variance = 100)
    de_anoms = [
        {
            "id": "POS-DE-1",
            "header_gross": "1000.00",
            "line_gross": "800.00",
            "price": "800.00",
            "date": (base_date + timedelta(days=40)).strftime("%Y-%m-%d"),
        },
        {
            "id": "POS-DE-2",
            "header_gross": "500.00",
            "line_gross": "600.00",
            "price": "600.00",
            "date": (base_date + timedelta(days=50)).strftime("%Y-%m-%d"),
        },
    ]
    for an in de_anoms:
        csv_rows.append(
            {
                "transaction_id": an["id"],
                "transaction_date": an["date"],
                "vendor_name": "QuickPOS Retailers",
                "vendor_vat": "4112233445",
                "gross_amount": an["header_gross"],
                "payment_method": "Cash",
                "item_description": "Bulk printer papers",
                "quantity": "1",
                "unit_price": an["line_gross"],
                "line_gross_amount": an["line_gross"],
                "tax_category": "standard",
                "declared_vat": str(
                    round_decimal(
                        Decimal(an["line_gross"])
                        - round_decimal(Decimal(an["line_gross"]) / Decimal("1.15"))
                    )
                ),
            }
        )

    # POS Corrupted Rows (written directly to test failure isolation)
    # Corrupted 1: Missing transaction_id
    csv_rows.append(
        {
            "transaction_id": "",
            "transaction_date": "2026-05-01",
            "vendor_name": "QuickPOS Retailers",
            "vendor_vat": "4112233445",
            "gross_amount": "100.00",
            "payment_method": "Cash",
            "item_description": "Item",
            "quantity": "1",
            "unit_price": "100.00",
            "line_gross_amount": "100.00",
            "tax_category": "standard",
            "declared_vat": "13.04",
        }
    )
    # Corrupted 2: Future date
    csv_rows.append(
        {
            "transaction_id": "POS-ERR-1",
            "transaction_date": "2029-01-01",
            "vendor_name": "QuickPOS Retailers",
            "vendor_vat": "4112233445",
            "gross_amount": "100.00",
            "payment_method": "Cash",
            "item_description": "Item",
            "quantity": "1",
            "unit_price": "100.00",
            "line_gross_amount": "100.00",
            "tax_category": "standard",
            "declared_vat": "13.04",
        }
    )
    # Corrupted 3: Invalid 9-digit VAT number (should be 10)
    csv_rows.append(
        {
            "transaction_id": "POS-ERR-2",
            "transaction_date": "2026-05-01",
            "vendor_name": "QuickPOS Retailers",
            "vendor_vat": "123456789",
            "gross_amount": "100.00",
            "payment_method": "Cash",
            "item_description": "Item",
            "quantity": "1",
            "unit_price": "100.00",
            "line_gross_amount": "100.00",
            "tax_category": "standard",
            "declared_vat": "13.04",
        }
    )

    # Write POS CSV
    keys = csv_rows[0].keys()
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(csv_rows)

    # -------------------------------------------------------------------------
    # 2. Generate Mock E-Commerce JSON
    # Nested structure: transaction header containing nested line items list
    # -------------------------------------------------------------------------
    # Let's generate 40 JSON transactions:
    # - 36 compliant
    # - 2 with VAT anomalies
    # - 2 with Double-Entry anomalies
    # - We will also insert 2 corrupted records in the JSON (e.g. negative price, invalid VAT)
    # -------------------------------------------------------------------------
    json_data = []

    # Compliant JSON transactions
    for i in range(36):
        tx_id = f"JSON-{2000 + i}"
        tx_date = (base_date + timedelta(days=random.randint(0, 100))).strftime(
            "%Y-%m-%d"
        )
        pm = random.choice(["PayPal", "Credit Card"])

        # 3 lines: 1 standard, 1 zero_rated (petrol), 1 exempt (bus ticket)
        item1_gross = Decimal(random.randint(200, 2000))
        item1_vat = round_decimal(
            item1_gross - round_decimal(item1_gross / Decimal("1.15"))
        )

        item2_gross = Decimal(random.randint(50, 500))
        item2_vat = Decimal("0.00")

        item3_gross = Decimal(random.randint(100, 300))
        item3_vat = Decimal("0.00")

        gross_total = item1_gross + item2_gross + item3_gross

        json_data.append(
            {
                "transaction_id": tx_id,
                "transaction_date": tx_date,
                "vendor_name": "E-Cart Distributors",
                "vendor_vat": "4998877665",
                "gross_amount": str(gross_total),
                "payment_method": pm,
                "line_items": [
                    {
                        "item_description": "Electronics Charger",
                        "quantity": 1,
                        "unit_price": str(item1_gross),
                        "line_gross_amount": str(item1_gross),
                        "tax_category": "standard",
                        "declared_vat": str(item1_vat),
                    },
                    {
                        "item_description": "Zero-rated diesel fuel",
                        "quantity": 2,
                        "unit_price": str(round(item2_gross / 2, 2)),
                        "line_gross_amount": str(item2_gross),
                        "tax_category": "zero_rated",
                        "declared_vat": str(item2_vat),
                    },
                    {
                        "item_description": "Exempt passenger bus ticket",
                        "quantity": 1,
                        "unit_price": str(item3_gross),
                        "line_gross_amount": str(item3_gross),
                        "tax_category": "exempt",
                        "declared_vat": str(item3_vat),
                    },
                ],
            }
        )

    # JSON VAT anomalies
    # Anomaly 1: standard-rated, Gross=1150 (expected VAT = 150), declared VAT = 50 (variance = 100)
    # Anomaly 2: zero-rated, Gross=500 (expected VAT = 0), declared VAT = 80 (variance = 80)
    json_data.append(
        {
            "transaction_id": "JSON-ANOM-1",
            "transaction_date": (base_date + timedelta(days=10)).strftime("%Y-%m-%d"),
            "vendor_name": "E-Cart Distributors",
            "vendor_vat": "4998877665",
            "gross_amount": "1150.00",
            "payment_method": "Credit Card",
            "line_items": [
                {
                    "item_description": "Monitor Stand",
                    "quantity": 1,
                    "unit_price": "1150.00",
                    "line_gross_amount": "1150.00",
                    "tax_category": "standard",
                    "declared_vat": "50.00",
                }
            ],
        }
    )
    json_data.append(
        {
            "transaction_id": "JSON-ANOM-2",
            "transaction_date": (base_date + timedelta(days=20)).strftime("%Y-%m-%d"),
            "vendor_name": "E-Cart Distributors",
            "vendor_vat": "4998877665",
            "gross_amount": "500.00",
            "payment_method": "PayPal",
            "line_items": [
                {
                    "item_description": "Fresh milk cartons",
                    "quantity": 10,
                    "unit_price": "50.00",
                    "line_gross_amount": "500.00",
                    "tax_category": "zero_rated",
                    "declared_vat": "80.00",
                }
            ],
        }
    )

    # JSON Double-Entry Anomalies
    # Anomaly 3: Header Gross = 2000, Line Gross = 1800 (variance = 200)
    # Anomaly 4: Header Gross = 1500, Line Gross = 1650 (variance = 150)
    json_data.append(
        {
            "transaction_id": "JSON-DE-1",
            "transaction_date": (base_date + timedelta(days=40)).strftime("%Y-%m-%d"),
            "vendor_name": "E-Cart Distributors",
            "vendor_vat": "4998877665",
            "gross_amount": "2000.00",
            "payment_method": "Credit Card",
            "line_items": [
                {
                    "item_description": "Office Desk chair",
                    "quantity": 1,
                    "unit_price": "1800.00",
                    "line_gross_amount": "1800.00",
                    "tax_category": "standard",
                    "declared_vat": str(
                        round_decimal(
                            Decimal("1800.00")
                            - round_decimal(Decimal("1800.00") / Decimal("1.15"))
                        )
                    ),
                }
            ],
        }
    )
    json_data.append(
        {
            "transaction_id": "JSON-DE-2",
            "transaction_date": (base_date + timedelta(days=50)).strftime("%Y-%m-%d"),
            "vendor_name": "E-Cart Distributors",
            "vendor_vat": "4998877665",
            "gross_amount": "1500.00",
            "payment_method": "PayPal",
            "line_items": [
                {
                    "item_description": "Storage Cabinets",
                    "quantity": 1,
                    "unit_price": "1650.00",
                    "line_gross_amount": "1650.00",
                    "tax_category": "standard",
                    "declared_vat": str(
                        round_decimal(
                            Decimal("1650.00")
                            - round_decimal(Decimal("1650.00") / Decimal("1.15"))
                        )
                    ),
                }
            ],
        }
    )

    # JSON Corrupted payloads
    # Corrupted 1: Negative Gross Amount
    json_data.append(
        {
            "transaction_id": "JSON-ERR-1",
            "transaction_date": "2026-05-01",
            "vendor_name": "E-Cart Distributors",
            "vendor_vat": "4998877665",
            "gross_amount": "-500.00",
            "payment_method": "PayPal",
            "line_items": [
                {
                    "item_description": "Refunded drive",
                    "quantity": 1,
                    "unit_price": "-500.00",
                    "line_gross_amount": "-500.00",
                    "tax_category": "standard",
                    "declared_vat": "-65.22",
                }
            ],
        }
    )
    # Corrupted 2: Invalid 10-digit VAT constraint fail (letters in VAT number)
    json_data.append(
        {
            "transaction_id": "JSON-ERR-2",
            "transaction_date": "2026-05-01",
            "vendor_name": "E-Cart Distributors",
            "vendor_vat": "499887766A",
            "gross_amount": "100.00",
            "payment_method": "PayPal",
            "line_items": [
                {
                    "item_description": "Adapter",
                    "quantity": 1,
                    "unit_price": "100.00",
                    "line_gross_amount": "100.00",
                    "tax_category": "standard",
                    "declared_vat": "13.04",
                }
            ],
        }
    )

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2)

    logger.info("Generated mock flat POS CSV at: %s", csv_path)
    logger.info("Generated mock nested e-commerce JSON at: %s", json_path)


def clean_database(db_conn):
    """
    Cleans database records before executing the ingestion pipeline.
    Bypasses immutable ledger constraints by removing SQLite db or dropping PG tables.
    """
    project_root = os.path.dirname(os.path.abspath(__file__))
    if db_conn.db_type == "sqlite":
        db_path = os.path.join(project_root, "database", "ledger_vault.db")
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
                logger.info("SQLite database file removed for clean run.")
            except Exception as e:
                logger.warning("Could not remove SQLite database file: %s", e)
    else:
        conn = db_conn.connect()
        cursor = conn.cursor()
        try:
            cursor.execute("DROP TABLE IF EXISTS line_items CASCADE;")
            cursor.execute("DROP TABLE IF EXISTS transactions CASCADE;")
            cursor.execute("DROP TABLE IF EXISTS vendors CASCADE;")
            conn.commit()
            logger.info("PostgreSQL tables dropped for clean run.")

            schema_path = os.path.join(project_root, "database", "schema.sql")
            with open(schema_path, "r", encoding="utf-8") as f:
                schema_sql = f.read()
            cursor.execute(schema_sql)
            conn.commit()
            logger.info("PostgreSQL tables re-created from schema.sql.")
        except Exception as e:
            conn.rollback()
            logger.warning("PostgreSQL clean/recreate failed: %s", e)
        finally:
            conn.close()


def main():
    project_root = os.path.dirname(os.path.abspath(__file__))

    # 1. Reset DLQ logs file
    dlq_path = os.path.join(project_root, "logs", "dead_letter.json")
    if os.path.exists(dlq_path):
        os.remove(dlq_path)

    # 2. Generate clean mock inputs
    generate_mock_inputs(project_root)

    # 3. Setup Database Connection
    db_conn = DatabaseConnection()
    clean_database(db_conn)

    # 4. Execute Component 1 Ingestion loaders
    logger.info("Step 1: Parsing and normalizing multi-format streams...")
    csv_file = os.path.join(project_root, "data", "raw_pos.csv")
    json_file = os.path.join(project_root, "data", "raw_ecommerce.json")

    csv_ingested, csv_failed = parse_csv_file(csv_file, db_conn)
    json_ingested, json_failed = parse_json_file(json_file, db_conn)

    # 5. Execute Component 3 Algorithmic Auditor
    logger.info("Step 2: Executing tax compliance matrix auditor...")
    auditor = AlgorithmicAuditor(db_conn)
    audit_findings = auditor.audit()

    # 6. Execute Component 4 Corporate Reporting & Ledger Export
    logger.info("Step 3: Generating compliance audit report package...")
    report_gen_path = os.path.join(project_root, "audit_report.json")
    report = generate_corporate_report(audit_findings, report_gen_path)

    # 7. Verification Assertions
    print("\n" + "=" * 60)
    print("      INTEGRATION VERIFICATION METRICS")
    print("=" * 60)

    # Ingestion metrics
    expected_csv_ingested = 40
    expected_json_ingested = 40
    expected_dlq_rows = 5

    success = True

    if csv_ingested == expected_csv_ingested:
        print(
            f" PASS: CSV POS loader successfully ingested {csv_ingested} transactions."
        )
    else:
        print(
            f" FAIL: CSV POS loader discrepancy. Expected {expected_csv_ingested}, got {csv_ingested}"
        )
        success = False

    if json_ingested == expected_json_ingested:
        print(
            f" PASS: JSON E-commerce loader successfully ingested {json_ingested} transactions."
        )
    else:
        print(
            f" FAIL: JSON E-commerce loader discrepancy. Expected {expected_json_ingested}, got {json_ingested}"
        )
        success = False

    # Read DLQ rows
    with open(dlq_path, "r", encoding="utf-8") as f:
        dlq_lines = f.readlines()
    actual_dlq_rows = len(dlq_lines)

    if actual_dlq_rows == expected_dlq_rows:
        print(
            f" PASS: dead_letter.json DLQ isolated exactly {actual_dlq_rows} corrupted records."
        )
    else:
        print(
            f" FAIL: dead_letter.json DLQ discrepancy. Expected {expected_dlq_rows}, got {actual_dlq_rows}"
        )
        success = False

    actual_de = len(report["anomalies_log"]["double_entry_imbalances"])
    actual_vat = len(report["anomalies_log"]["vat_compliance_violations"])
    actual_variance = report["audit_summary"]["total_vat_variance_detected_zar"]

    if actual_de == 4:
        print(f" PASS: Identified exactly {actual_de} Double-Entry balance anomalies.")
    else:
        print(f" FAIL: Double-Entry anomalies count. Expected 4, got {actual_de}")
        success = False

    if actual_vat == 5:
        print(f" PASS: Identified exactly {actual_vat} VAT Compliance Violations.")
    else:
        print(f" FAIL: VAT violations count. Expected 5, got {actual_vat}")
        success = False

    if abs(actual_variance - 400.00) < 0.001:
        print(f" PASS: Total audited VAT Variance is R {actual_variance:.2f} ZAR.")
    else:
        print(
            f" FAIL: VAT Variance discrepancy. Expected R 400.00, got R {actual_variance:.2f}"
        )
        success = False

    print("=" * 60)
    if success:
        print(" APEX ENGINE VERIFICATION COMPLETED: ALL INTEGRATION PATHS PASS")
    else:
        print(" APEX ENGINE VERIFICATION FAILED: REVIEW SYSTEM LOGS")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
