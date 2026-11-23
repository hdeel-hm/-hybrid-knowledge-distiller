import gc
import os
import json
import time
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vault_storage import KnowledgeVault
from distiller_core import KnowledgeDistiller

app = FastAPI(
    title="Hybrid Deterministic Knowledge Distiller API",
    description="منظومة تقطير المعرفة واستخراج المعادلات والقواعد المنطقية لتقليل تكاليف الذكاء الاصطناعي بنسبة 90%.",
    version="1.0.0"
)

vault = KnowledgeVault()
distiller = KnowledgeDistiller(vault=vault)

# حماية ضد الضغط العالي (Rate Limiting: 30 طلب/دقيقة لكل IP)
_rate_store: dict[str, list[float]] = {}
MAX_REQ_PER_MIN = 30
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # الحد الأقصى للملف 10MB


def check_rate_limit(client_ip: str) -> None:
    now = time.time()
    if len(_rate_store) > 300:
        stale = [ip for ip, ts in _rate_store.items() if not ts or (now - ts[-1]) > 60]
        for ip in stale:
            del _rate_store[ip]

    history = [t for t in _rate_store.get(client_ip, []) if (now - t) < 60]
    if len(history) >= MAX_REQ_PER_MIN:
        raise HTTPException(status_code=429, detail="تم تجاوز الحد المسموح به للطلبات (30 طلب/دقيقة).")
    history.append(now)
    _rate_store[client_ip] = history


class TextIngestRequest(BaseModel):
    book_title: str
    chapter: str = "عام"
    text: str


@app.get("/")
def home():
    return {
        "system": "Hybrid Deterministic Knowledge Distiller",
        "status": "online",
        "docs_url": "/docs",
        "architecture": "Zero-Bloat Python + SQLite FTS5 + PyMuPDF Streaming"
    }


@app.post("/ingest/text")
def ingest_text(payload: TextIngestRequest, request: Request):
    """تقطير نص مباشر أو فصل من كتاب وحفظ المعادلات والقواعد في الخزنة."""
    check_rate_limit(request.client.host if request.client else "unknown")
    start = time.perf_counter()

    report = distiller.distill_text_chunk(
        book_title=payload.book_title,
        raw_text=payload.text,
        chapter=payload.chapter
    )
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    gc.collect()

    return {
        "status": "success",
        "book_title": payload.book_title,
        "processing_time_ms": elapsed_ms,
        "extraction_report": report
    }


@app.post("/ingest/pdf")
async def ingest_pdf(
    request: Request,
    book_title: str = Form(...),
    file: UploadFile = File(...)
):
    """تقطير كتاب PDF كامل عبر القراءة المتدفقة بأقل استهلاك للذاكرة."""
    check_rate_limit(request.client.host if request.client else "unknown")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف بصيغة PDF فقط.")

    start = time.perf_counter()
    pdf_bytes = await file.read()
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        del pdf_bytes
        gc.collect()
        raise HTTPException(status_code=413, detail="حجم ملف PDF يتجاوز الحد الآمن (10MB).")

    try:
        report = distiller.distill_pdf_bytes(book_title=book_title, pdf_bytes=pdf_bytes)
    finally:
        del pdf_bytes
        gc.collect()

    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    return {
        "status": "success",
        "processing_time_ms": elapsed_ms,
        "extraction_report": report
    }


@app.get("/query")
def query_knowledge(q: str, limit: int = 5, request: Request = None):
    """
    البحث الفوري في الخزنة المحلية بتكلفة صفر.
    يعيد ESCALATE_TO_AI فقط إذا لم تتوفر المعلومة محلياً.
    """
    if request and request.client:
        check_rate_limit(request.client.host)

    start = time.perf_counter()
    raw_json = vault.query_vault(query_text=q, limit=limit)
    parsed = json.loads(raw_json)
    parsed["query_time_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return JSONResponse(content=parsed)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
  
