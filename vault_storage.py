import json
import sqlite3
import hashlib
from datetime import datetime


class KnowledgeVault:
    """
    خزنة المعرفة المعزولة (Workspace-Isolated Knowledge Vault)
    تحفظ بيانات كل مستخدم أو كتاب في مساحته الخاصة بتكلفة صفر وبحث فوري.
    """

    def __init__(self, db_path: str = "distilled_knowledge_v2.db"):
        self.db_path = db_path
        self._init_storage()

    def _init_storage(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_records (
                    record_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    book_title TEXT NOT NULL,
                    chapter TEXT,
                    item_type TEXT NOT NULL,
                    exact_text TEXT NOT NULL,
                    keywords TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    workspace_id,
                    book_title,
                    chapter,
                    item_type,
                    exact_text,
                    keywords
                )
            """)
            conn.commit()

    @staticmethod
    def _compute_id(workspace_id: str, text: str) -> str:
        normalized = f"{workspace_id.strip().lower()}::{' '.join(text.strip().lower().split())}"
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def store_item(
        self,
        book_title: str,
        item_type: str,
        exact_text: str,
        chapter: str = "عام",
        keywords: list[str] | None = None,
        workspace_id: str = "public_demo"
    ) -> dict:
        clean_text = exact_text.strip()
        ws = "".join(c for c in workspace_id if c.isalnum() or c in ("_", "-")).strip() or "public_demo"
        if not clean_text:
            return {"status": "ignored"}

        rec_id = self._compute_id(ws, clean_text)
        kw_str = ", ".join(keywords) if keywords else ""

        with sqlite3.connect(self.db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM knowledge_records WHERE record_id = ?", (rec_id,)
            ).fetchone()

            if exists:
                return {"status": "CACHE_HIT"}

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO knowledge_records
                (record_id, workspace_id, book_title, chapter, item_type, exact_text, keywords, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rec_id, ws, book_title, chapter, item_type, clean_text, kw_str, now_str)
            )
            conn.execute(
                """
                INSERT INTO knowledge_fts
                (workspace_id, book_title, chapter, item_type, exact_text, keywords)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ws, book_title, chapter, item_type, clean_text, kw_str)
            )
            conn.commit()

        return {"status": "STORED"}

    def query_vault(self, query_text: str, workspace_id: str = "public_demo", limit: int = 8) -> dict:
        ws = "".join(c for c in workspace_id if c.isalnum() or c in ("_", "-")).strip() or "public_demo"
        clean_q = "".join(ch for ch in query_text if ch.isalnum() or ch.isspace()).strip()
        tokens = [f"{t}*" for t in clean_q.split() if len(t) > 1]

        if not tokens:
            return {"status": "EMPTY_QUERY", "results": []}

        fts_query = f'workspace_id:"{ws}" AND (' + " OR ".join(tokens) + ")"

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT book_title, chapter, item_type, exact_text
                FROM knowledge_fts
                WHERE knowledge_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit)
            ).fetchall()

        if not rows:
            return {
                "status": "ESCALATE_TO_AI",
                "message": "المعلومة غير موجودة في مساحتك الخاصة؛ يمكنك الآن الاستعانة بالذكاء الاصطناعي."
            }

        return {
            "status": "LOCAL_HIT",
            "cost": "$0.00",
            "results": [
                {"book": r, "location": r, "type": r, "content": r}
                for r in rows
            ]
        }
        
