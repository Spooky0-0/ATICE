import json
import os
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


def write_to_dlq(raw_payload, error_reason, error_code, dlq_path):
    entry = {
        "ingested_at": datetime.utcnow().isoformat() + "Z",
        "error_code": error_code,
        "error_reason": error_reason,
        "source_format": "JSON",
        "raw_payloads": [raw_payload],
    }
    with open(dlq_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def parse_json_file(filepath, db_conn):
    """
    Parses modern JSON e-commerce exports with nested transaction headers and line items.
    """
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    dlq_path = os.path.join(project_root, "logs", "dead_letter.json")
    os.makedirs(os.path.dirname(dlq_path), exist_ok=True)

    with open(filepath, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception as e:
            write_to_dlq(
                {"file_content": "Malformed JSON file"},
                f"Failed to parse file: {e}",
                "ERR_JSON_FILE_CORRUPT",
                dlq_path,
            )
            return 0, 1

    conn = db_conn.connect()
    cursor = conn.cursor()

    current_date = datetime.now().date()
    inserted_tx = 0
    corrupted_tx = 0

    # Process each nested transaction block
    for tx_payload in data:
        try:
            tx_id = tx_payload.get("transaction_id", "").strip()
            if not tx_id:
                raise ValueError("Missing transaction_id (Primary Key)")

            raw_date = tx_payload.get("transaction_date", "").strip()
            tx_date = clean_date(raw_date)
            if not tx_date:
                raise ValueError(f"Invalid transaction date: '{raw_date}'")

            # Date boundaries
            if tx_date > current_date:
                raise ValueError(f"Future date rejected: {tx_date}")
            if tx_date < current_date - timedelta(days=3 * 365):
                raise ValueError(f"Closed period backdating: {tx_date}")

            vendor_name = tx_payload.get("vendor_name", "").strip()
            vendor_vat = tx_payload.get("vendor_vat", "").strip()
            if not vendor_name or not vendor_vat:
                raise ValueError("Missing vendor_name or vendor_vat")
            if not re.match(r"^\d{10}$", vendor_vat):
                raise ValueError(
                    f"Invalid VAT registration number format: '{vendor_vat}'"
                )

            raw_gross = tx_payload.get("gross_amount", "")
            tx_gross = clean_currency(raw_gross)
            if tx_gross is None or tx_gross < 0:
                raise ValueError(f"Invalid transaction gross amount: '{raw_gross}'")

            payment_method = tx_payload.get("payment_method", "Unknown").strip()

            raw_lines = tx_payload.get("line_items", [])
            if not raw_lines:
                raise ValueError("Transaction has no line items")

            lines_to_insert = []
            for line_payload in raw_lines:
                desc = line_payload.get("item_description", "").strip()
                if not desc:
                    raise ValueError("Missing item description on line")

                raw_qty = line_payload.get("quantity", 1)
                try:
                    qty = int(raw_qty)
                except Exception:
                    raise ValueError(f"Invalid quantity: '{raw_qty}'")
                if qty <= 0:
                    raise ValueError(f"Quantity must be positive: {qty}")

                raw_price = line_payload.get("unit_price", "")
                price = clean_currency(raw_price)
                if price is None or price < 0:
                    raise ValueError(f"Invalid unit price: '{raw_price}'")

                raw_line_gross = line_payload.get("line_gross_amount", "")
                line_gross = clean_currency(raw_line_gross)
                if line_gross is None:
                    line_gross = round_decimal(Decimal(qty) * price)

                tax_cat = line_payload.get("tax_category", "standard").strip().lower()
                if tax_cat not in ("standard", "zero_rated", "exempt"):
                    raise ValueError(f"Unknown tax category: '{tax_cat}'")

                raw_declared_vat = line_payload.get("declared_vat", "")
                declared_vat = clean_currency(raw_declared_vat)
                if declared_vat is None:
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
                        "JSON",
                    ),
                )
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
                        "JSON",
                    ),
                )

            # Check if transaction already has line items
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
            write_to_dlq(tx_payload, str(e), "ERR_JSON_INGEST_FAIL", dlq_path)
            corrupted_tx += 1

    conn.close()
    logger.info(
        "JSON Ingestion: Ingested Transactions=%d, Corrupted/Rejected=%d",
        inserted_tx,
        corrupted_tx,
    )
    return inserted_tx, corrupted_tx
