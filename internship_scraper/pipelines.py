import logging
import sqlite3
from datetime import datetime

DB_PATH = "offers.db"
logger = logging.getLogger(__name__)
COMMIT_EVERY = 10  # on valide souvent pour ne pas bloquer la base pendant tout le scraping


class SQLitePipeline:
    def open_spider(self, spider=None):
        self.conn = sqlite3.connect(DB_PATH, timeout=30)
        self.conn.execute("PRAGMA busy_timeout=30000")
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                offer_id TEXT NOT NULL,
                url TEXT,
                title TEXT,
                company TEXT,
                location TEXT,
                contract TEXT,
                description TEXT,
                content TEXT,
                date_text TEXT,
                category TEXT,
                nb_links INTEGER,
                listing_page INTEGER,
                first_seen TEXT,
                last_seen TEXT,
                notified INTEGER DEFAULT 0,
                UNIQUE (source, offer_id)
            )
            """
        )
        self.conn.commit()
        self.new_count = 0
        self.seen_count = 0
        self.pending = 0
        self.source = "?"

    def process_item(self, item, spider=None):
        now = datetime.now().isoformat(timespec="seconds")
        self.source = item["source"]
        row = self.conn.execute(
            "SELECT id FROM offers WHERE source = ? AND offer_id = ?",
            (item["source"], item["offer_id"]),
        ).fetchone()

        if row:
            self.conn.execute("UPDATE offers SET last_seen = ? WHERE id = ?", (now, row[0]))
            self.seen_count += 1
        else:
            self.conn.execute(
                """
                INSERT INTO offers (source, offer_id, url, title, company, location,
                    contract, description, content, date_text, category, nb_links,
                    listing_page, first_seen, last_seen, notified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    item["source"], item["offer_id"], item.get("url"), item.get("title"),
                    item.get("company"), item.get("location"), item.get("contract"),
                    item.get("description"), item.get("content"), item.get("date_text"),
                    item.get("category"), item.get("nb_links"), item.get("listing_page"),
                    now, now,
                ),
            )
            self.new_count += 1

        self.pending += 1
        if self.pending >= COMMIT_EVERY:
            self.conn.commit()
            self.pending = 0
        return item

    def close_spider(self, spider=None):
        self.conn.commit()
        self.conn.close()
        logger.info(
            f"BILAN BASE [{self.source}] : {self.new_count} NOUVELLES offres, "
            f"{self.seen_count} deja connues"
        )