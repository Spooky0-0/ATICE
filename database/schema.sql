-- ============================================================================
-- Project Apex-Audit-Engine ($APEX$) - Relational Vault Schema
-- Target: PostgreSQL 11+ (Immutability enforced via triggers)
-- ============================================================================

DROP TABLE IF EXISTS line_items CASCADE;
DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS vendors CASCADE;

-- Table 1: vendors (SME and suppliers profiles)
CREATE TABLE vendors (
    vendor_id SERIAL PRIMARY KEY,
    vendor_name VARCHAR(255) UNIQUE NOT NULL,
    -- South African VAT Registration Numbers are exactly 10 digits
    vat_registration_number VARCHAR(10) NOT NULL,
    compliance_status VARCHAR(50) NOT NULL DEFAULT 'Compliant',
    CONSTRAINT chk_vat_reg_len CHECK (vat_registration_number ~ '^\d{10}$')
);

-- Table 2: transactions (Aggregated transactions)
CREATE TABLE transactions (
    transaction_id VARCHAR(100) PRIMARY KEY,
    vendor_id INTEGER NOT NULL REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
    transaction_date DATE NOT NULL,
    gross_amount NUMERIC(12, 2) NOT NULL,
    payment_method VARCHAR(50) NOT NULL,
    source_format VARCHAR(20) NOT NULL, -- e.g., 'CSV', 'JSON'
    ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_trans_gross_positive CHECK (gross_amount >= 0)
);

-- Table 3: line_items (Individual items inside transactions - one-to-many relationship)
CREATE TABLE line_items (
    line_item_id SERIAL PRIMARY KEY,
    transaction_id VARCHAR(100) NOT NULL REFERENCES transactions(transaction_id) ON DELETE CASCADE,
    item_description VARCHAR(255) NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0),
    gross_amount NUMERIC(12, 2) NOT NULL, -- quantity * unit_price (inclusive of VAT if applicable)
    tax_category VARCHAR(20) NOT NULL CHECK (tax_category IN ('standard', 'zero_rated', 'exempt')),
    declared_vat NUMERIC(12, 2) NOT NULL CHECK (declared_vat >= 0),
    CONSTRAINT chk_line_gross CHECK (gross_amount >= 0)
);

-- Indexes for performance and auditing queries
CREATE INDEX idx_trans_vendor_id ON transactions(vendor_id);
CREATE INDEX idx_trans_date ON transactions(transaction_date);
CREATE INDEX idx_line_items_trans_id ON line_items(transaction_id);
CREATE INDEX idx_line_items_tax_cat ON line_items(tax_category);

-- ============================================================================
-- Immutability Triggers (Enforce Append-Only Database Vault)
-- ============================================================================

CREATE OR REPLACE FUNCTION prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Ledger Vault is immutable. Transaction and line item modifications are strictly forbidden.';
END;
$$ LANGUAGE plpgsql;

-- Apply to transactions
CREATE TRIGGER trg_prevent_trans_update BEFORE UPDATE ON transactions FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
CREATE TRIGGER trg_prevent_trans_delete BEFORE DELETE ON transactions FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();

-- Apply to line_items
CREATE TRIGGER trg_prevent_line_update BEFORE UPDATE ON line_items FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
CREATE TRIGGER trg_prevent_line_delete BEFORE DELETE ON line_items FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
