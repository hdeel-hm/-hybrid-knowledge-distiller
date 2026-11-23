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
    r'(?:^|[\.\!\؟\n])\s*(?:إذا|كلما|عندما|بشرط|في حال|يجب أن|لا يجوز|إشارة خطر|الخلاصة الاستشارية|If|When|Whenever|Unless|Rule|Red Flag)\b[^\.\!\؟\n]{12,260}[\.\!\؟]?',
    re.IGNORECASE
)

COMPLIANCE_PATTERN = re.compile(
    r'[^\.\!\؟\n]{5,120}(?:السعودية|موثوق|الإمارات|السورية|المرسوم التشريعي|الأردنية|المواصفات والمقاييس|مصر|حماية المستهلك|الضرائب|FTC|GDPR)[^\.\!\؟\n]{10,200}[\.\!\؟]?',
    re.IGNORECASE
)

STAT_PATTERN = re.compile(
    r'[^\.\!\؟\n]{8,160}(?:\d+(?:\.\d+)?%|\d+(?:\.\d+)?x|\$\d+(?:,\d+)*|\d+\s*(?:مليون|مليار|ألف|million|billion))[^\.\!\؟\n]{5,140}[\.\!\؟]?',
    re.IGNORECASE
)

URL_PATTERN = re.compile(r'https?://[^\s\)\]\>\"\']+')

COMMON_TYPOS = {
    "إستراتيجية": "استراتيجية (بدون همزة)",
    "لأمتلاك": "لامتلاك (بدون همزة)",
    "إقتصادية": "اقتصادية (بدون همزة)",
    "إعلانية": "إعلانية (صحيحة) - تأكد من عدم تكرارها خطأً"
}

STOPWORDS = {
    "في", "من", "على", "إلى", "عن", "مع", "هذا", "هذه", "ذلك", "تلك", "التي", "الذي",
    "أن", "إن", "كان", "كانت", "يكون", "بين", "أو", "ثم", "حتى", "إذا", "عند", "كل",
    "the", "and", "for", "that", "this", "with", "from", "are", "was", "were", "have"
}


class KnowledgeDistiller:
    """
    محرك تقطير المعرفة الحتمي (VIP Enterprise Edition)
    مخصص لاستخراج القواعد الاستشارية، التشريعات الإقليمية، المعادلات، وتدقيق النصوص بأقل رام.
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
        report = {
            "equations_stored": 0,
            "rules_stored": 0,
            "regulations_stored": 0,
            "stats_stored": 0,
            "cache_hits": 0,
            "broken_links": [],
            "typo_warnings": []
        }

        if not raw_text or not raw_text.strip():
            return report

        # 1. تدقيق الهفوات الإملائية الشائعة
        for wrong_word, correction in COMMON_TYPOS.items():
            if wrong_word in raw_text and "صحيحة" not in correction:
                report["typo_warnings"].append(f"[{chapter}] تم رصد '{wrong_word}' -> يُنصح بـ: {correction}")

        # 2. فحص الروابط
        for url in set(URL_PATTERN.findall(raw_text)):
            if not self.verify_url(url):
                report["broken_links"].append(url)

        # 3. استخراج المعادلات
        for match in set(EQUATION_PATTERN.findall(raw_text)):
            clean_eq = " ".join(match.split())
            res = self.vault.store_item(book_title, "معادلة / صيغة", clean_eq, chapter, self._extract_keywords(clean_eq))
            if res["status"] == "STORED":
                report["equations_stored"] += 1
            elif res["status"] == "CACHE_HIT":
                report["cache_hits"] += 1

        # 4. استخراج القواعد الاستشارية وإشارات الخطر
        for match in set(RULE_PATTERN.findall(raw_text)):
            clean_rule = " ".join(match.split())
            res = self.vault.store_item(book_title, "قاعدة استشارية / إشارة خطر", clean_rule, chapter, self._extract_keywords(clean_rule))
            if res["status"] == "STORED":
                report["rules_stored"] += 1
            elif res["status"] == "CACHE_HIT":
                report["cache_hits"] += 1

        # 5. استخراج التشريعات والحوكمة الإقليمية والدولية
        for match in set(COMPLIANCE_PATTERN.findall(raw_text)):
            clean_reg = " ".join(match.split())
            res = self.vault.store_item(book_title, "تشريع وحوكمة إقليمية", clean_reg, chapter, self._extract_keywords(clean_reg))
            if res["status"] == "STORED":
                report["regulations_stored"] += 1
            elif res["status"] == "CACHE_HIT":
                report["cache_hits"] += 1

        # 6. استخراج الإحصائيات والمؤشرات الاقتصادية
        for match in set(STAT_PATTERN.findall(raw_text)):
            clean_stat = " ".join(match.split())
            res = self.vault.store_item(book_title, "مؤشر اقتصادي / إحصائية", clean_stat, chapter, self._extract_keywords(clean_stat))
            if res["status"] == "STORED":
                report["stats_stored"] += 1
            elif res["status"] == "CACHE_HIT":
                report["cache_hits"] += 1

        return report

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
        total = {
            "book_title": book_title,
            "pages_processed": 0,
            "equations_stored": 0,
            "rules_stored": 0,
            "regulations_stored": 0,
            "stats_stored": 0,
            "cache_hits": 0,
            "broken_links": [],
            "typo_warnings": []
        }

        broken_set = set()
        for page_num, page_text in self._stream_pdf_pages(pdf_bytes):
            total["pages_processed"] += 1
            res = self.distill_text_chunk(book_title, page_text, chapter=f"صفحة {page_num}")
            total["equations_stored"] += res["equations_stored"]
            total["rules_stored"] += res["rules_stored"]
            total["regulations_stored"] += res["regulations_stored"]
            total["stats_stored"] += res["stats_stored"]
            total["cache_hits"] += res["cache_hits"]
            total["typo_warnings"].extend(res["typo_warnings"])
            broken_set.update(res["broken_links"])

        total["broken_links"] = list(broken_set)
        self._checked_urls_cache.clear()
        gc.collect()
        return total
            
