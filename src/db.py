import os
import sqlite3
import logging
from decimal import Decimal

# Register SQLite adapters and converters for Decimal type safety
sqlite3.register_adapter(Decimal, lambda d: str(d))
sqlite3.register_converter("NUMERIC", lambda s: Decimal(s.decode("ascii")))
sqlite3.register_converter("numeric", lambda s: Decimal(s.decode("ascii")))

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """
    Manages connection and schema setup for PostgreSQL and SQLite fallback.
    Maintains append-only constraints in both dialects.
    """

    def __init__(self):
        self.db_type = "sqlite"
        self.conn = None

        # Load environment variables for PostgreSQL connection
        self.host = os.getenv("PGHOST")
        self.database = os.getenv("PGDATABASE", "ledger_vault")
        self.user = os.getenv("PGUSER")
        self.password = os.getenv("PGPASSWORD")
        self.port = os.getenv("PGPORT", "5432")

        if self.host and self.user:
            self.db_type = "postgresql"

    def connect(self):
        if self.db_type == "postgresql":
            try:
                try:
                    import psycopg

                    self.conn = psycopg.connect(
                        host=self.host,
                        dbname=self.database,
                        user=self.user,
                        password=self.password,
                        port=self.port,
                    )
                except ImportError:
                    import psycopg2

                    self.conn = psycopg2.connect(
                        host=self.host,
                        database=self.database,
                        user=self.user,
                        password=self.password,
                        port=self.port,
                    )
                return self.conn
            except Exception as e:
                logger.warning(
                    "PostgreSQL connection failed: %s. Falling back to SQLite.", e
                )
                self.db_type = "sqlite"

        # Fallback to local SQLite database
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_path = os.path.join(project_root, "database", "ledger_vault.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self.conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self.initialize_sqlite_schema()
        return self.conn

    def initialize_sqlite_schema(self):
        """
        Sets up vendors, transactions, and line_items tables and indexes
        in SQLite with validation triggers that reject updates and deletions.
        """
        cursor = self.conn.cursor()

        # Table 1: vendors
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            vendor_id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_name TEXT UNIQUE NOT NULL,
            vat_registration_number TEXT NOT NULL CHECK(length(vat_registration_number) = 10),
            compliance_status TEXT NOT NULL DEFAULT 'Compliant'
        );
        """)

        # Table 2: transactions
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY,
            vendor_id INTEGER NOT NULL REFERENCES vendors(vendor_id) ON DELETE RESTRICT,
            transaction_date TEXT NOT NULL,
            gross_amount NUMERIC NOT NULL,
            payment_method TEXT NOT NULL,
            source_format TEXT NOT NULL,
            ingested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (gross_amount >= 0)
        );
        """)

        # Table 3: line_items
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS line_items (
            line_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id) ON DELETE CASCADE,
            item_description TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            unit_price NUMERIC NOT NULL CHECK (unit_price >= 0),
            gross_amount NUMERIC NOT NULL CHECK (gross_amount >= 0),
            tax_category TEXT NOT NULL CHECK (tax_category IN ('standard', 'zero_rated', 'exempt')),
            declared_vat NUMERIC NOT NULL CHECK (declared_vat >= 0)
        );
        """)

        # Indexes for analytical queries
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trans_vendor_id ON transactions(vendor_id);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_trans_date ON transactions(transaction_date);"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_line_items_trans_id ON line_items(transaction_id);"
        )

        # Append-Only Trigger Enforcements
        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_trans_update BEFORE UPDATE ON transactions
        BEGIN
            SELECT RAISE(ABORT, 'Ledger Vault is immutable. Transaction updates are strictly forbidden.');
        END;
        """)

        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_trans_delete BEFORE DELETE ON transactions
        BEGIN
            SELECT RAISE(ABORT, 'Ledger Vault is immutable. Transaction deletions are strictly forbidden.');
        END;
        """)

        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_line_update BEFORE UPDATE ON line_items
        BEGIN
            SELECT RAISE(ABORT, 'Ledger Vault is immutable. Line item updates are strictly forbidden.');
        END;
        """)

        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_line_delete BEFORE DELETE ON line_items
        BEGIN
            SELECT RAISE(ABORT, 'Ledger Vault is immutable. Line item deletions are strictly forbidden.');
        END;
        """)

        self.conn.commit()
