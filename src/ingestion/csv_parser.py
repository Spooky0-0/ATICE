import csv
import os
import json
import re
import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)


def round_decimal(val):
    if val is None:
        return None
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def clean_currency(val):
    if not val or str(val).strip() == "":
        return None
    # Remove R, commas, spaces, and other non-numeric symbols except dot and minus
    cleaned = str(val).replace(",", "").strip()
    cleaned = re.sub(r"[^\d\.\-]", "", cleaned)
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def clean_date(val):
    if not val or str(val).strip() == "":
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(val).strip(), fmt).date()
        except ValueError:
            continue
    return None


def write_to_dlq(raw_rows, error_reason, error_code, dlq_path):
    entry = {
        "ingested_at": datetime.utcnow().isoformat() + "Z",
        "error_code": error_code,
        "error_reason": error_reason,
        "source_format": "CSV",
        "raw_payloads": raw_rows,
    }
    with open(dlq_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def parse_csv_file(filepath, db_conn):
    """
    Parses flat CSV transaction logs, groups by transaction_id,
    reconciles header/lines, and batch-inserts into the Relational Vault.
    """
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    dlq_path = os.path.join(project_root, "logs", "dead_letter.json")
    os.makedirs(os.path.dirname(dlq_path), exist_ok=True)

    # Read flat CSV rows
    raw_tx_groups = {}
    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for raw_row in reader:
            tx_id = raw_row.get("transaction_id", "").strip()
            if not tx_id:
                write_to_dlq(
                    [raw_row], "Missing transaction_id", "ERR_NULL_PK", dlq_path
                )
                continue
            if tx_id not in raw_tx_groups:
                raw_tx_groups[tx_id] = []
            raw_tx_groups[tx_id].append(raw_row)

    conn = db_conn.connect()
    cursor = conn.cursor()

    current_date = datetime.now().date()
    inserted_tx = 0
    corrupted_tx = 0

    # Process each transaction group
    for tx_id, rows in raw_tx_groups.items():
        try:
            # Validate Header Fields (common across rows)
            first_row = rows[0]
            raw_date = first_row.get("transaction_date", "").strip()
            tx_date = clean_date(raw_date)
            if not tx_date:
                raise ValueError(f"Invalid transaction date: '{raw_date}'")

            # Date boundaries
            if tx_date > current_date:
                raise ValueError(f"Future date rejected: {tx_date}")
            if tx_date < current_date - timedelta(days=3 * 365):
                raise ValueError(f"Closed period backdating: {tx_date}")

            vendor_name = first_row.get("vendor_name", "").strip()
            vendor_vat = first_row.get("vendor_vat", "").strip()
            if not vendor_name or not vendor_vat:
                raise ValueError("Missing vendor_name or vendor_vat")
            if not re.match(r"^\d{10}$", vendor_vat):
                raise ValueError(
                    f"Invalid VAT registration number length or format: '{vendor_vat}'"
                )

            raw_gross = first_row.get("gross_amount", "")
            tx_gross = clean_currency(raw_gross)
            if tx_gross is None or tx_gross < 0:
                raise ValueError(f"Invalid transaction gross amount: '{raw_gross}'")

            payment_method = first_row.get("payment_method", "Unknown").strip()

            # Validate Line Items
            lines_to_insert = []
            for row in rows:
                desc = row.get("item_description", "").strip()
                if not desc:
                    raise ValueError("Missing item description on line")

                raw_qty = row.get("quantity", "1").strip()
                try:
                    qty = int(float(raw_qty)) if raw_qty else 1
                except Exception:
                    raise ValueError(f"Invalid quantity: '{raw_qty}'")
                if qty <= 0:
                    raise ValueError(f"Quantity must be positive: {qty}")

                raw_price = row.get("unit_price", "")
                price = clean_currency(raw_price)
                if price is None or price < 0:
                    raise ValueError(f"Invalid unit price: '{raw_price}'")

                # line gross amount (quantity * unit_price)
                raw_line_gross = row.get("line_gross_amount", "")
                line_gross = clean_currency(raw_line_gross)
                if line_gross is None:
                    line_gross = round_decimal(Decimal(qty) * price)

                tax_cat = row.get("tax_category", "standard").strip().lower()
                if tax_cat not in ("standard", "zero_rated", "exempt"):
                    raise ValueError(f"Unknown tax category: '{tax_cat}'")

                raw_declared_vat = row.get("declared_vat", "")
                declared_vat = clean_currency(raw_declared_vat)
                if declared_vat is None:
                    # Default VAT calculations
                    if tax_cat == "standard":
                        declared_vat = round_decimal(
                            line_gross - round_decimal(line_gross / Decimal("1.15"))
                        )
                    else:
                        declared_vat = Decimal("0.00")
                else:
                    declared_vat = round_decimal(declared_vat)

                lines_to_insert.append(
                    {
                        "item_description": desc,
                        "quantity": qty,
                        "unit_price": price,
                        "gross_amount": line_gross,
                        "tax_category": tax_cat,
                        "declared_vat": declared_vat,
                    }
                )

            # Database Ingest
            # 1. Insert Vendor
            if db_conn.db_type == "postgresql":
                cursor.execute(
                    "INSERT INTO vendors (vendor_name, vat_registration_number) VALUES (%s, %s) "
                    "ON CONFLICT (vendor_name) DO UPDATE SET compliance_status = 'Compliant' "
                    "RETURNING vendor_id;",
                    (vendor_name, vendor_vat),
                )
                vendor_id = cursor.fetchone()[0]
            else:
                cursor.execute(
                    "INSERT OR IGNORE INTO vendors (vendor_name, vat_registration_number) VALUES (?, ?);",
                    (vendor_name, vendor_vat),
                )
                cursor.execute(
                    "SELECT vendor_id FROM vendors WHERE vendor_name = ?;",
                    (vendor_name,),
                )
                vendor_id = cursor.fetchone()[0]

            # 2. Insert Transaction
            if db_conn.db_type == "postgresql":
                cursor.execute(
                    "INSERT INTO transactions (transaction_id, vendor_id, transaction_date, gross_amount, payment_method, source_format) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (transaction_id) DO NOTHING;",
                    (
                        tx_id,
                        vendor_id,
                        tx_date.strftime("%Y-%m-%d"),
                        tx_gross,
                        payment_method,
                        "CSV",
                    ),
                )
                # Check if row was inserted (in PG rowcount is returned)
                # If transaction exists, triggers prevent update.
            else:
                cursor.execute(
                    "INSERT OR IGNORE INTO transactions (transaction_id, vendor_id, transaction_date, gross_amount, payment_method, source_format) "
                    "VALUES (?, ?, ?, ?, ?, ?);",
                    (
                        tx_id,
                        vendor_id,
                        tx_date.strftime("%Y-%m-%d"),
                        tx_gross,
                        payment_method,
                        "CSV",
                    ),
                )

            # Check if transaction was actually inserted or if it was ignored
            # In SQLite, we can verify by checking if changes() > 0 or if we can query it.
            # To be safe, we try inserting line items. If transaction_id exists and trigger blocks update, it's fine.
            # However, since transaction_id is a primary key, line items link to it. If it was IGNORED, inserting line items
            # might violate foreign key constraint if transaction didn't exist, but since it did exist, we shouldn't insert duplicate line items.
            # Let's check if the transaction already has line items.
            if db_conn.db_type == "postgresql":
                cursor.execute(
                    "SELECT 1 FROM line_items WHERE transaction_id = %s LIMIT 1;",
                    (tx_id,),
                )
            else:
                cursor.execute(
                    "SELECT 1 FROM line_items WHERE transaction_id = ? LIMIT 1;",
                    (tx_id,),
                )

            if cursor.fetchone():
                # Already ingested, skip inserting duplicate line items
                continue

            # 3. Insert Line Items
            for line in lines_to_insert:
                if db_conn.db_type == "postgresql":
                    cursor.execute(
                        "INSERT INTO line_items (transaction_id, item_description, quantity, unit_price, gross_amount, tax_category, declared_vat) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s);",
                        (
                            tx_id,
                            line["item_description"],
                            line["quantity"],
                            line["unit_price"],
                            line["gross_amount"],
                            line["tax_category"],
                            line["declared_vat"],
                        ),
                    )
                else:
                    cursor.execute(
                        "INSERT INTO line_items (transaction_id, item_description, quantity, unit_price, gross_amount, tax_category, declared_vat) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?);",
                        (
                            tx_id,
                            line["item_description"],
                            line["quantity"],
                            line["unit_price"],
                            line["gross_amount"],
                            line["tax_category"],
                            line["declared_vat"],
                        ),
                    )

            conn.commit()
            inserted_tx += 1

        except Exception as e:
            conn.rollback()
            write_to_dlq(rows, str(e), "ERR_CSV_INGEST_FAIL", dlq_path)
            corrupted_tx += 1

    conn.close()
    logger.info(
        "CSV Ingestion: Ingested Transactions=%d, Corrupted/Rejected=%d",
        inserted_tx,
        corrupted_tx,
    )
    return inserted_tx, corrupted_tx
