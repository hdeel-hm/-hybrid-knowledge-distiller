import gc
import os
import time
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from vault_storage import KnowledgeVault
from distiller_core import (
    KnowledgeDistiller, EQUATION_PATTERN, RULE_PATTERN,
    COMPLIANCE_PATTERN, STAT_PATTERN, URL_PATTERN, COMMON_TYPOS
)

app = FastAPI(title="Smart Knowledge Distiller")

vault = KnowledgeVault()
distiller = KnowledgeDistiller(vault=vault)

_rate_store: dict[str, list[float]] = {}
MAX_REQ_PER_MIN = 40
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def check_rate_limit(client_ip: str) -> None:
    now = time.time()
    history = [t for t in _rate_store.get(client_ip, []) if (now - t) < 60]
    if len(history) >= MAX_REQ_PER_MIN:
        raise HTTPException(status_code=429, detail="يرجى الانتظار دقيقة قبل إرسال طلبات إضافية.")
    history.append(now)
    _rate_store[client_ip] = history


def run_distillation(book_title: str, raw_text: str, chapter: str, workspace_id: str) -> dict:
    report = {
        "equations": [], "rules": [], "regulations": [], "stats": [],
        "broken_links": [], "typos": [], "stored_count": 0, "cached_count": 0
    }

    for wrong_word, correction in COMMON_TYPOS.items():
        if wrong_word in raw_text and "صحيحة" not in correction:
            report["typos"].append(f"[{chapter}] كلمة '{wrong_word}' -> الأصح: {correction}")

    for url in set(URL_PATTERN.findall(raw_text)):
        if not distiller.verify_url(url):
            report["broken_links"].append(url)

    categories = [
        (EQUATION_PATTERN, "معادلة / صيغة", "equations"),
        (RULE_PATTERN, "قاعدة استشارية / إشارة خطر", "rules"),
        (COMPLIANCE_PATTERN, "تشريع وحوكمة", "regulations"),
        (STAT_PATTERN, "مؤشر اقتصادي / إحصائية", "stats"),
    ]

    for pattern, label, key in categories:
        for match in set(pattern.findall(raw_text)):
            clean_item = " ".join(match.split())
            report[key].append(clean_item)
            res = vault.store_item(
                book_title=book_title,
                item_type=label,
                exact_text=clean_item,
                chapter=chapter,
                keywords=distiller._extract_keywords(clean_item),
                workspace_id=workspace_id
            )
            if res.get("status") == "STORED":
                report["stored_count"] += 1
            elif res.get("status") == "CACHE_HIT":
                report["cached_count"] += 1

    return report


class TextPayload(BaseModel):
    workspace_id: str = "demo"
    book_title: str = "مرجع عام"
    text: str


@app.get("/")
def serve_frontend():
    return FileResponse("index.html", media_type="text/html")


@app.post("/api/distill-text")
def api_distill_text(payload: TextPayload, request: Request):
    check_rate_limit(request.client.host if request.client else "unknown")
    start = time.perf_counter()
    res = run_distillation(payload.book_title, payload.text, "نص مباشر", payload.workspace_id)
    res["time_ms"] = round((time.perf_counter() - start) * 1000, 2)
    gc.collect()
    return res


@app.post("/api/distill-pdf")
async def api_distill_pdf(
    request: Request,
    workspace_id: str = Form("demo"),
    book_title: str = Form("كتاب PDF"),
    file: UploadFile = File(...)
):
    check_rate_limit(request.client.host if request.client else "unknown")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="يرجى اختيار ملف PDF فقط.")

    start = time.perf_counter()
    pdf_bytes = await file.read()
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        del pdf_bytes
        gc.collect()
        raise HTTPException(status_code=413, detail="حجم الملف يتجاوز 15MB.")

    merged = {
        "pages": 0, "equations": [], "rules": [], "regulations": [],
        "stats": [], "broken_links": [], "typos": [], "stored_count": 0, "cached_count": 0
    }

    try:
        for page_num, page_text in distiller._stream_pdf_pages(pdf_bytes):
            merged["pages"] += 1
            step = run_distillation(book_title, page_text, f"صفحة {page_num}", workspace_id)
            for k in ("equations", "rules", "regulations", "stats", "broken_links", "typos"):
                merged[k].extend(step[k])
            merged["stored_count"] += step["stored_count"]
            merged["cached_count"] += step["cached_count"]
    finally:
        del pdf_bytes
        gc.collect()

    merged["broken_links"] = list(set(merged["broken_links"]))
    merged["time_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return merged


@app.get("/api/search")
def api_search(q: str, workspace_id: str = "demo", request: Request = None):
    if request and request.client:
        check_rate_limit(request.client.host)
    start = time.perf_counter()
    data = vault.query_vault(query_text=q, workspace_id=workspace_id)
    data["time_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return data


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
            
