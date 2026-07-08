import os
import json
from decimal import Decimal, ROUND_HALF_UP


def round_decimal(val):
    if val is None:
        return None
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class AlgorithmicAuditor:
    """
    Executes deep analytical audits on transactions and line items.
    Validates VAT recalculations against standard/zero/exempt rules
    and checks double-entry balance consistency.
    """

    def __init__(self, db_conn, config_path=None):
        self.db_conn = db_conn

        # Load tax rules config
        if config_path is None:
            project_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            config_path = os.path.join(project_root, "config", "tax_rules.json")

        with open(config_path, "r", encoding="utf-8") as f:
            self.tax_config = json.load(f)

        self.standard_rate = Decimal(str(self.tax_config.get("standard_rate", "0.15")))
        self.rounding_tolerance = Decimal("0.05")

    def audit(self):
        """
        Runs the analytical audit across the relational ledger vault.
        """
        conn = self.db_conn.connect()
        cursor = conn.cursor()

        # Audit results structure
        audit_summary = {
            "total_vendors_audited": 0,
            "total_transactions_audited": 0,
            "total_line_items_audited": 0,
            "compliant_transactions": 0,
            "flagged_double_entry_anomalies": [],
            "flagged_vat_compliance_anomalies": [],
            "total_vat_variance_detected": Decimal("0.00"),
            "total_gross_audited": Decimal("0.00"),
            "high_risk_vendors": {},
        }

        # 1. Fetch Vendors
        cursor.execute(
            "SELECT vendor_id, vendor_name, vat_registration_number FROM vendors;"
        )
        vendors = cursor.fetchall()
        audit_summary["total_vendors_audited"] = len(vendors)

        vendor_map = {v[0]: {"name": v[1], "vat": v[2]} for v in vendors}

        # 2. Fetch Transactions
        cursor.execute(
            "SELECT transaction_id, vendor_id, transaction_date, gross_amount, payment_method FROM transactions;"
        )
        transactions = cursor.fetchall()
        audit_summary["total_transactions_audited"] = len(transactions)

        for tx in transactions:
            tx_id, vendor_id, tx_date, tx_gross, pm = tx
            tx_gross = Decimal(str(tx_gross))
            audit_summary["total_gross_audited"] += tx_gross

            vendor_info = vendor_map.get(
                vendor_id, {"name": "Unknown", "vat": "0000000000"}
            )
            v_name = vendor_info["name"]

            # Fetch line items for this transaction
            (
                cursor.execute(
                    "SELECT line_item_id, item_description, quantity, unit_price, gross_amount, tax_category, declared_vat "
                    "FROM line_items WHERE transaction_id = ?;",
                    (tx_id,),
                )
                if self.db_conn.db_type == "sqlite"
                else cursor.execute(
                    "SELECT line_item_id, item_description, quantity, unit_price, gross_amount, tax_category, declared_vat "
                    "FROM line_items WHERE transaction_id = %s;",
                    (tx_id,),
                )
            )

            line_items = cursor.fetchall()
            audit_summary["total_line_items_audited"] += len(line_items)

            # Sub-task A: Double-entry consistency check
            line_gross_sum = Decimal("0.00")
            tx_has_anomaly = False

            for line in line_items:
                l_id, desc, qty, u_price, l_gross, tax_cat, dec_vat = line
                l_gross = Decimal(str(l_gross))
                dec_vat = Decimal(str(dec_vat))

                line_gross_sum += l_gross

                # Sub-task B: VAT Recalculation Check
                # Recalculate Expected VAT based on tax category rules
                if tax_cat == "standard":
                    # Expected Net = Gross / 1.15
                    expected_net = round_decimal(
                        l_gross / (Decimal("1.00") + self.standard_rate)
                    )
                    expected_vat = round_decimal(l_gross - expected_net)
                else:
                    # zero_rated or exempt items must have 0 expected VAT
                    expected_vat = Decimal("0.00")

                vat_variance = abs(dec_vat - expected_vat)

                if vat_variance > self.rounding_tolerance:
                    tx_has_anomaly = True
                    anomaly_detail = {
                        "transaction_id": tx_id,
                        "vendor_name": v_name,
                        "line_item_id": l_id,
                        "item_description": desc,
                        "tax_category": tax_cat,
                        "gross_amount": float(l_gross),
                        "declared_vat": float(dec_vat),
                        "expected_vat": float(expected_vat),
                        "vat_variance": float(vat_variance),
                    }
                    audit_summary["flagged_vat_compliance_anomalies"].append(
                        anomaly_detail
                    )
                    audit_summary["total_vat_variance_detected"] += vat_variance

                    # Accumulate vendor risk
                    if v_name not in audit_summary["high_risk_vendors"]:
                        audit_summary["high_risk_vendors"][v_name] = {
                            "vat_number": vendor_info["vat"],
                            "anomalous_transactions_count": 0,
                            "total_vat_variance": Decimal("0.00"),
                        }
                    audit_summary["high_risk_vendors"][v_name][
                        "total_vat_variance"
                    ] += vat_variance

            # Double-entry balance check
            double_entry_diff = abs(tx_gross - line_gross_sum)
            if double_entry_diff > Decimal("0.00"):
                tx_has_anomaly = True
                de_anomaly = {
                    "transaction_id": tx_id,
                    "vendor_name": v_name,
                    "transaction_gross": float(tx_gross),
                    "line_items_gross_sum": float(line_gross_sum),
                    "variance": float(double_entry_diff),
                }
                audit_summary["flagged_double_entry_anomalies"].append(de_anomaly)

                # Flag vendor risk
                if v_name not in audit_summary["high_risk_vendors"]:
                    audit_summary["high_risk_vendors"][v_name] = {
                        "vat_number": vendor_info["vat"],
                        "anomalous_transactions_count": 0,
                        "total_vat_variance": Decimal("0.00"),
                    }

            if tx_has_anomaly:
                if v_name in audit_summary["high_risk_vendors"]:
                    audit_summary["high_risk_vendors"][v_name][
                        "anomalous_transactions_count"
                    ] += 1
            else:
                audit_summary["compliant_transactions"] += 1

        # Format Decimal attributes to standard python formats for serialization
        audit_summary["total_vat_variance_detected"] = float(
            audit_summary["total_vat_variance_detected"]
        )
        audit_summary["total_gross_audited"] = float(
            audit_summary["total_gross_audited"]
        )

        # Format vendor decimal lists
        for v_name, v_risk in list(audit_summary["high_risk_vendors"].items()):
            v_risk["total_vat_variance"] = float(v_risk["total_vat_variance"])

        # Update Vendor Compliance State in Database based on audit results
        for v_name, v_risk in audit_summary["high_risk_vendors"].items():
            if (
                v_risk["anomalous_transactions_count"] > 0
                or v_risk["total_vat_variance"] > 100.00
            ):
                # Update database compliance state to 'High Risk'
                if self.db_conn.db_type == "postgresql":
                    cursor.execute(
                        "UPDATE vendors SET compliance_status = 'High Risk' WHERE vendor_name = %s;",
                        (v_name,),
                    )
                else:
                    cursor.execute(
                        "UPDATE vendors SET compliance_status = 'High Risk' WHERE vendor_name = ?;",
                        (v_name,),
                    )

        conn.commit()
        conn.close()

        return audit_summary
