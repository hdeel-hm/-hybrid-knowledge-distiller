import gc
import io
import re
import urllib.request
from typing import Generator
from pypdf import PdfReader

from vault_storage import KnowledgeVault


EQUATION_PATTERN = re.compile(
    r'(?:[A-Za-zأ-ي_][A-Za-z0-9أ-ي_\s\(\)]{1,30}\s*=\s*[^=\n]{3,80}|'
    r'(?:معادلة|صيغة|Formula|Equation)\s*[:\-]\s*[^\n]{4,100})',
    re.IGNORECASE
)

RULE_PATTERN = re.compile(
    r'(?:^|[\.\!\؟\n])\s*(?:إذا|كلما|عندما|بشرط|في حال|يجب أن|لا يجوز|If|When|Whenever|Unless|Rule)\b[^\.\!\؟\n]{12,220}[\.\!\؟]?',
    re.IGNORECASE
)

STAT_PATTERN = re.compile(
    r'[^\.\!\؟\n]{8,160}(?:\d+(?:\.\d+)?%|\d+(?:\.\d+)?x|\$\d+(?:,\d+)*|\d+\s*(?:مليون|مليار|ألف|million|billion))[^\.\!\؟\n]{5,140}[\.\!\؟]?',
    re.IGNORECASE
)

URL_PATTERN = re.compile(r'https?://[^\s\)\]\>\"\']+')

STOPWORDS = {
    "في", "من", "على", "إلى", "عن", "مع", "هذا", "هذه", "ذلك", "تلك", "التي", "الذي",
    "أن", "إن", "كان", "كانت", "يكون", "بين", "أو", "ثم", "حتى", "إذا", "عند", "كل",
    "the", "and", "for", "that", "this", "with", "from", "are", "was", "were", "have",
    "has", "had", "not", "but", "what", "when", "where", "which", "who", "how", "than"
}


class KnowledgeDistiller:
    """
    محرك تقطير المعرفة الحتمي (Deterministic Knowledge Distiller)
    يستخرج المعادلات، القواعد المنطقية، والإحصائيات من الكتب والملفات بأقل استهلاك للذاكرة.
    """

    def __init__(self, vault: KnowledgeVault | None = None):
        self.vault = vault or KnowledgeVault()
        self._checked_urls_cache: dict[str, bool] = {}

    @staticmethod
    def _extract_keywords(text: str, max_words: int = 6) -> list[str]:
        tokens = re.findall(r'\b[أ-يa-zA-Z]{3,}\b', text.lower())
        freq: dict[str, int] = {}
        for word in tokens:
            if word not in STOPWORDS:
                freq[word] = freq.get(word, 0) + 1
        return sorted(freq, key=freq.get, reverse=True)[:max_words]

    def verify_url(self, url: str) -> bool:
        if url in self._checked_urls_cache:
            return self._checked_urls_cache[url]

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as response:
                is_ok = response.status < 400
        except Exception:
            is_ok = False

        self._checked_urls_cache[url] = is_ok
        return is_ok

    def distill_text_chunk(self, book_title: str, raw_text: str, chapter: str = "عام") -> dict:
        stats_summary = {
            "equations_stored": 0,
            "rules_stored": 0,
            "stats_stored": 0,
            "cache_hits": 0,
            "broken_links": []
        }

        if not raw_text or not raw_text.strip():
            return stats_summary

        for url in set(URL_PATTERN.findall(raw_text)):
            if not self.verify_url(url):
                stats_summary["broken_links"].append(url)

        for match in set(EQUATION_PATTERN.findall(raw_text)):
            clean_eq = " ".join(match.split())
            res = self.vault.store_item(
                book_title=book_title,
                item_type="معادلة رياضية / صيغة",
                exact_text=clean_eq,
                chapter=chapter,
                keywords=self._extract_keywords(clean_eq)
            )
            if res["status"] == "STORED":
                stats_summary["equations_stored"] += 1
            elif res["status"] == "CACHE_HIT":
                stats_summary["cache_hits"] += 1

        for match in set(RULE_PATTERN.findall(raw_text)):
            clean_rule = " ".join(match.split())
            res = self.vault.store_item(
                book_title=book_title,
                item_type="قاعدة منطقية / شرطية",
                exact_text=clean_rule,
                chapter=chapter,
                keywords=self._extract_keywords(clean_rule)
            )
            if res["status"] == "STORED":
                stats_summary["rules_stored"] += 1
            elif res["status"] == "CACHE_HIT":
                stats_summary["cache_hits"] += 1

        for match in set(STAT_PATTERN.findall(raw_text)):
            clean_stat = " ".join(match.split())
            res = self.vault.store_item(
                book_title=book_title,
                item_type="إحصائية / معيار رقمي",
                exact_text=clean_stat,
                chapter=chapter,
                keywords=self._extract_keywords(clean_stat)
            )
            if res["status"] == "STORED":
                stats_summary["stats_stored"] += 1
            elif res["status"] == "CACHE_HIT":
                stats_summary["cache_hits"] += 1

        return stats_summary

    def _stream_pdf_pages(self, pdf_bytes: bytes) -> Generator[tuple[int, str], None, None]:
        stream = io.BytesIO(pdf_bytes)
        reader = PdfReader(stream)
        try:
            for page_idx, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                yield page_idx + 1, text
                del page, text
        finally:
            stream.close()
            del reader, stream
            gc.collect()

    def distill_pdf_bytes(self, book_title: str, pdf_bytes: bytes) -> dict:
        total_report = {
            "book_title": book_title,
            "pages_processed": 0,
            "equations_stored": 0,
            "rules_stored": 0,
            "stats_stored": 0,
            "cache_hits": 0,
            "broken_links": []
        }

        broken_set = set()
        for page_num, page_text in self._stream_pdf_pages(pdf_bytes):
            total_report["pages_processed"] += 1
            chunk_res = self.distill_text_chunk(
                book_title=book_title,
                raw_text=page_text,
                chapter=f"صفحة {page_num}"
            )
            total_report["equations_stored"] += chunk_res["equations_stored"]
            total_report["rules_stored"] += chunk_res["rules_stored"]
            total_report["stats_stored"] += chunk_res["stats_stored"]
            total_report["cache_hits"] += chunk_res["cache_hits"]
            broken_set.update(chunk_res["broken_links"])

        total_report["broken_links"] = list(broken_set)
        self._checked_urls_cache.clear()
        gc.collect()
        return total_report
        
