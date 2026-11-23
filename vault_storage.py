import json
import sqlite3
import hashlib
from datetime import datetime


class KnowledgeVault:
    """
    خزنة المعرفة الحتمية (Deterministic Knowledge Vault)
    تعتمد على محرك SQLite FTS5 المدمج لتوفير البحث الفوري بتكلفة صفر ومنع تكرار المعالجة.
    """

    def __init__(self, db_path: str = "distilled_knowledge.db"):
        self.db_path = db_path
        self._init_storage()

    def _init_storage(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            # جدول التخزين الأساسي ومنع التكرار عبر البصمة الرقمية
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_records (
                    content_hash TEXT PRIMARY KEY,
                    book_title TEXT NOT NULL,
                    chapter TEXT,
                    item_type TEXT NOT NULL,
                    exact_text TEXT NOT NULL,
                    keywords TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            # جدول البحث النصي فائق السرعة (FTS5)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    content_hash UNINDEXED,
                    book_title,
                    item_type,
                    exact_text,
                    keywords
                )
            """)
            conn.commit()

    @staticmethod
    def _compute_hash(text: str) -> str:
        normalized = " ".join(text.strip().lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    def store_item(
        self,
        book_title: str,
        item_type: str,
        exact_text: str,
        chapter: str = "عام",
        keywords: list[str] | None = None
    ) -> dict:
        """
        حفظ معلومة مستخرجة (معادلة، قاعدة منطقية، أو إحصائية) مع منع التكرار.
        """
        clean_text = exact_text.strip()
        if not clean_text:
            return {"status": "ignored", "reason": "نص فارغ"}

        content_hash = self._compute_hash(clean_text)
        kw_str = ", ".join(keywords) if keywords else ""

        with sqlite3.connect(self.db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM knowledge_records WHERE content_hash = ?",
                (content_hash,)
            ).fetchone()

            if exists:
                return {
                    "status": "CACHE_HIT",
                    "hash": content_hash,
                    "message": "المعلومة مفهرسة مسبقاً (تم توفير المعالجة)."
                }

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO knowledge_records 
                (content_hash, book_title, chapter, item_type, exact_text, keywords, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (content_hash, book_title, chapter, item_type, clean_text, kw_str, now_str)
            )
            conn.execute(
                """
                INSERT INTO knowledge_fts 
                (content_hash, book_title, item_type, exact_text, keywords)
                VALUES (?, ?, ?, ?, ?)
                """,
                (content_hash, book_title, item_type, clean_text, kw_str)
            )
            conn.commit()

        return {
            "status": "STORED",
            "hash": content_hash,
            "type": item_type
        }

    def query_vault(self, query_text: str, limit: int = 5) -> str:
        """
        البحث الفوري في الخزنة. إذا لم تتوفر المعلومة يطلب تدخل الذكاء الاصطناعي.
        """
        clean_q = "".join(ch for ch in query_text if ch.isalnum() or ch.isspace()).strip()
        if not clean_q:
            return json.dumps({
                "status": "ESCALATE_TO_AI",
                "reason": "استعلام غير صالح أو فارغ."
            }, ensure_ascii=False)

        # بناء استعلام FTS5 مرن يبحث عن أي كلمة مفتاحية مطابقة
        tokens = [f"{t}*" for t in clean_q.split() if len(t) > 1]
        if not tokens:
            return json.dumps({
                "status": "ESCALATE_TO_AI",
                "reason": "كلمات البحث قصيرة جداً."
            }, ensure_ascii=False)

        fts_query = " OR ".join(tokens)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT content_hash, book_title, item_type, exact_text, keywords
                FROM knowledge_fts
                WHERE knowledge_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit)
            ).fetchall()

        if not rows:
            return json.dumps({
                "status": "ESCALATE_TO_AI",
                "query": query_text,
                "message": "المعلومة غير متوفرة في الخزنة المحلية؛ يُنصح بتوجيه الطلب للذكاء الاصطناعي."
            }, ensure_ascii=False, indent=2)

        results = [
            {
                "hash": r[0],
                "book_title": r[1],
                "type": r[2],
                "content": r[3],
                "keywords": r[4]
            }
            for r in rows
        ]

        return json.dumps({
            "status": "LOCAL_HIT",
            "cost": "$0.00",
            "matches_count": len(results),
            "results": results
        }, ensure_ascii=False, indent=2)
          
