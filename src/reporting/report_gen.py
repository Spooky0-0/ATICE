import os
import json
from datetime import datetime


def generate_corporate_report(audit_results, output_path=None):
    """
    Transforms raw audit findings into a structured production audit package.
    """
    if output_path is None:
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        output_path = os.path.join(project_root, "audit_report.json")

    # Calculate additional corporate reporting metrics
    vat_anomalies_count = len(audit_results["flagged_vat_compliance_anomalies"])
    de_anomalies_count = len(audit_results["flagged_double_entry_anomalies"])
    total_anomalies = vat_anomalies_count + de_anomalies_count

    # Financial materiality assessment
    total_gross = audit_results["total_gross_audited"]
    # Materiality set at 0.1% of audited gross revenue
    materiality_threshold = round(total_gross * 0.001, 2)
    total_variance = audit_results["total_vat_variance_detected"]
    materiality_exceeded = total_variance > materiality_threshold

    # Sort high risk vendors by total vat variance desc
    sorted_risk_vendors = sorted(
        audit_results["high_risk_vendors"].items(),
        key=lambda x: x[1]["total_vat_variance"],
        reverse=True,
    )

    # Compile the production audit report
    report_data = {
        "audit_summary": {
            "audited_at": datetime.utcnow().isoformat() + "Z",
            "total_vendors_audited": audit_results["total_vendors_audited"],
            "total_transactions_audited": audit_results["total_transactions_audited"],
            "total_line_items_audited": audit_results["total_line_items_audited"],
            "compliant_transactions": audit_results["compliant_transactions"],
            "anomalous_transactions_count": total_anomalies,
            "total_gross_audited_zar": total_gross,
            "total_vat_variance_detected_zar": total_variance,
            "materiality_threshold_zar": materiality_threshold,
            "materiality_exceeded": materiality_exceeded,
        },
        "risk_analysis": {
            "high_risk_vendors": [
                {
                    "vendor_name": name,
                    "vat_registration_number": info["vat_number"],
                    "anomalous_transactions": info["anomalous_transactions_count"],
                    "vat_variance_zar": info["total_vat_variance"],
                }
                for name, info in sorted_risk_vendors
            ]
        },
        "anomalies_log": {
            "vat_compliance_violations": audit_results[
                "flagged_vat_compliance_anomalies"
            ],
            "double_entry_imbalances": audit_results["flagged_double_entry_anomalies"],
        },
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    # Print Executive Terminal Dashboard
    print("\n" + "=" * 60)
    print("      APEX AUDITING EXECUTIVE REPORT")
    print("=" * 60)
    print(f" Audited At:          {report_data['audit_summary']['audited_at']}")
    print(f" Total Gross Audited: R {total_gross:,.2f} ZAR")
    print(
        f" Compliance Rate:     {((audit_results['compliant_transactions'] / max(1, total_gross)) * 100):.2f}%"
        if total_gross == 0
        else f" Compliance Rate:     {((audit_results['compliant_transactions'] / max(1, audit_results['total_transactions_audited'])) * 100):.2f}%"
    )
    print(f" Total Transactions:  {audit_results['total_transactions_audited']} parsed")
    print(f" Compliant Trans:     {audit_results['compliant_transactions']}")
    print(f" Anomalous Trans:     {total_anomalies}")
    print("-" * 60)
    print(f" Total VAT Variance:  R {total_variance:,.2f} ZAR")
    print(f" Materiality Limit:   R {materiality_threshold:,.2f} ZAR (0.1%)")
    if materiality_exceeded:
        print(" WARNING: Material compliance vulnerability detected.")
    else:
        print(" PASS: VAT discrepancies fall within materiality limits.")
    print("-" * 60)
    print(" High-Risk Vendors:")
    for idx, (v_name, v_info) in enumerate(sorted_risk_vendors[:5], start=1):
        print(
            f"  {idx}. {v_name} (VAT: {v_info['vat_number']}) - {v_info['anomalous_transactions_count']} anomalies | R {v_info['total_vat_variance']:,.2f} variance"
        )
    print("=" * 60 + "\n")

    return report_data
