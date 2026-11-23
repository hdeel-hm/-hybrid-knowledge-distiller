import gc
import os
import json
import time
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vault_storage import KnowledgeVault
from distiller_core import KnowledgeDistiller

app = FastAPI(
    title="VIP Executive Knowledge Distiller",
    description="خزنة المعرفة المحمية لاستخراج القواعد الاستشارية والتشريعات وتدقيق المراجع.",
    version="2.0.0"
)

vault = KnowledgeVault()
distiller = KnowledgeDistiller(vault=vault)

VIP_KEY = os.environ.get("VIP_SECRET_KEY", "HM-VIP-2026").strip()
_rate_store: dict[str, list[float]] = {}
MAX_REQ_PER_MIN = 30
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def verify_vip_access(vip_key: str | None) -> None:
    if not vip_key or vip_key.strip() != VIP_KEY:
        raise HTTPException(status_code=403, detail="وصول مرفوض: مفتاح الخزنة السري (VIP Key) غير صحيح.")


def check_rate_limit(client_ip: str) -> None:
    now = time.time()
    history = [t for t in _rate_store.get(client_ip, []) if (now - t) < 60]
    if len(history) >= MAX_REQ_PER_MIN:
        raise HTTPException(status_code=429, detail="تم تجاوز الحد المسموح به للطلبات.")
    history.append(now)
    _rate_store[client_ip] = history


class TextIngestRequest(BaseModel):
    book_title: str
    chapter: str = "عام"
    text: str


@app.get("/")
def home():
    return {"system": "VIP Executive Knowledge Distiller", "status": "protected_online"}


@app.post("/ingest/text")
def ingest_text(
    payload: TextIngestRequest,
    request: Request,
    x_vip_key: str | None = Header(default=None, alias="X-VIP-Key")
):
    verify_vip_access(x_vip_key)
    check_rate_limit(request.client.host if request.client else "unknown")
    start = time.perf_counter()
    report = distiller.distill_text_chunk(payload.book_title, payload.text, payload.chapter)
    gc.collect()
    return {
        "status": "success",
        "processing_time_ms": round((time.perf_counter() - start) * 1000, 2),
        "extraction_report": report
    }


@app.post("/ingest/pdf")
async def ingest_pdf(
    request: Request,
    book_title: str = Form(...),
    file: UploadFile = File(...),
    x_vip_key: str | None = Header(default=None, alias="X-VIP-Key")
):
    verify_vip_access(x_vip_key)
    check_rate_limit(request.client.host if request.client else "unknown")
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف PDF فقط.")

    start = time.perf_counter()
    pdf_bytes = await file.read()
    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        del pdf_bytes
        gc.collect()
        raise HTTPException(status_code=413, detail="حجم الملف يتجاوز 15MB.")

    try:
        report = distiller.distill_pdf_bytes(book_title, pdf_bytes)
    finally:
        del pdf_bytes
        gc.collect()

    return {
        "status": "success",
        "processing_time_ms": round((time.perf_counter() - start) * 1000, 2),
        "extraction_report": report
    }


@app.get("/query")
def query_knowledge(
    q: str,
    limit: int = 5,
    request: Request = None,
    x_vip_key: str | None = Header(default=None, alias="X-VIP-Key")
):
    verify_vip_access(x_vip_key)
    if request and request.client:
        check_rate_limit(request.client.host)

    start = time.perf_counter()
    parsed = json.loads(vault.query_vault(query_text=q, limit=limit))
    parsed["query_time_ms"] = round((time.perf_counter() - start) * 1000, 2)
    return JSONResponse(content=parsed)
    
