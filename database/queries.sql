-- ============================================================================
-- Project Apex-Audit-Engine ($APEX$) - Pre-Compiled Analytical Audit Queries
-- Target: PostgreSQL / ANSI SQL
-- ============================================================================

-- Query 1: Double-Entry Discrepancy Check
-- Identifies transactions where the sum of line item gross amounts does not match the transaction gross amount
-- Variance represents discrepancy in booking.
-- EXPLANATION: Helps detect leakage where line items and headers don't reconcile.
-- ----------------------------------------------------------------------------
SELECT 
    t.transaction_id,
    t.gross_amount AS transaction_gross,
    SUM(l.gross_amount) AS line_items_gross_sum,
    ABS(t.gross_amount - SUM(l.gross_amount)) AS double_entry_variance
FROM transactions t
JOIN line_items l ON t.transaction_id = l.transaction_id
GROUP BY t.transaction_id, t.gross_amount
HAVING ABS(t.gross_amount - SUM(l.gross_amount)) > 0.00
ORDER BY double_entry_variance DESC;


-- Query 2: Standard-Rated VAT Calculation Discrepancies
-- Recalculates expected standard-rate (15%) VAT and flags variances exceeding R0.05
-- Expected VAT = Gross - (Gross / 1.15)
-- ----------------------------------------------------------------------------
SELECT 
    l.line_item_id,
    l.transaction_id,
    l.item_description,
    l.gross_amount,
    l.declared_vat,
    ROUND(l.gross_amount - (l.gross_amount / 1.15), 2) AS expected_vat,
    ABS(l.declared_vat - ROUND(l.gross_amount - (l.gross_amount / 1.15), 2)) AS vat_variance
FROM line_items l
WHERE l.tax_category = 'standard'
  AND ABS(l.declared_vat - ROUND(l.gross_amount - (l.gross_amount / 1.15), 2)) > 0.05
ORDER BY vat_variance DESC;


-- Query 3: Illegal Taxing of Zero-Rated or Exempt Items
-- Identifies zero-rated or exempt items where VAT was declared (should be 0)
-- ----------------------------------------------------------------------------
SELECT 
    l.line_item_id,
    l.transaction_id,
    l.item_description,
    l.tax_category,
    l.gross_amount,
    l.declared_vat
FROM line_items l
WHERE l.tax_category IN ('zero_rated', 'exempt')
  AND l.declared_vat > 0.00
ORDER BY l.declared_vat DESC;


-- Query 4: Vendor Risk Analysis Profile
-- Aggregates compliance stats per vendor, highlighting total VAT variance, compliance rate, and risk ranking
-- ----------------------------------------------------------------------------
SELECT 
    v.vendor_name,
    v.vat_registration_number,
    COUNT(DISTINCT t.transaction_id) AS total_transactions,
    COUNT(CASE WHEN ABS(l.declared_vat - 
        (CASE 
            WHEN l.tax_category = 'standard' THEN ROUND(l.gross_amount - (l.gross_amount / 1.15), 2)
            ELSE 0.00
         END)) > 0.05 THEN 1 END) AS anomalous_line_items,
    SUM(ABS(l.declared_vat - 
        (CASE 
            WHEN l.tax_category = 'standard' THEN ROUND(l.gross_amount - (l.gross_amount / 1.15), 2)
            ELSE 0.00
         END))) AS total_vat_variance_detected
FROM vendors v
LEFT JOIN transactions t ON v.vendor_id = t.vendor_id
LEFT JOIN line_items l ON t.transaction_id = l.transaction_id
GROUP BY v.vendor_id, v.vendor_name, v.vat_registration_number
ORDER BY total_vat_variance_detected DESC;
