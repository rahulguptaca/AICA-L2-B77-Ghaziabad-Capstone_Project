"""Document processing pipeline: extract → render → AI verify → reconcile.

Status flow: uploaded → reading → extracting → rendering → ai_verifying →
reconciling → awaiting_review → verified → locked (failed on error).
AI unavailability never breaks the pipeline: extraction results are preserved
and items fall back to manual review."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import (
    AuditLog, Document, DocumentPage, ExtractionResult, FinancialLineItem,
    FinancialPeriod, VerificationResult,
)
from ..ai.provider import AIProviderError
from ..ai.service import get_provider, logged_call, get_ai_config
from ..financial.canonical import STATEMENT_OF
from .extractor import PdfExtraction, extract_pdf_items, extract_xlsx_items, inspect_pdf, render_pages

log = logging.getLogger(__name__)

MATCH_TOLERANCE = 0.005  # 0.5% → formatting-equivalent match


def _set_status(db: Session, doc: Document, status: str, error: str = "") -> None:
    doc.status = status
    if error:
        doc.error = error[:2000]
    db.commit()


def process_document(db: Session, doc: Document) -> dict[str, Any]:
    """Run the full pipeline for one uploaded document."""
    settings = get_settings()
    path = Path(settings.upload_dir) / doc.stored_filename
    if not path.exists():
        _set_status(db, doc, "failed", "Stored file missing")
        raise FileNotFoundError(str(path))

    try:
        _set_status(db, doc, "reading")
        is_pdf = doc.mime_type == "application/pdf" or doc.original_filename.lower().endswith(".pdf")

        if is_pdf:
            ext = inspect_pdf(path)
            doc.page_count = ext.page_count
            doc.has_native_text = 1 if ext.has_native_text else 0
            if not ext.has_native_text:
                # substantially scanned — no OCR in core pipeline; needs manual entry
                _set_status(db, doc, "failed",
                            "PDF appears to be scanned with no usable native text. "
                            "OCR is only attempted when no native text exists — please "
                            "upload a digital statement or enter values manually.")
                return {"status": "failed", "reason": "scanned_pdf"}
            _set_status(db, doc, "extracting")
            ext = extract_pdf_items(path, ext)
        else:
            _set_status(db, doc, "extracting")
            ext = extract_xlsx_items(path)
            doc.page_count = ext.page_count

        _store_pages_and_raw(db, doc, ext)

        rendered: dict[int, str] = {}
        if is_pdf:
            _set_status(db, doc, "rendering")
            candidates = [p.page_number for p in ext.pages if p.is_candidate][:8]
            rendered = render_pages(path, candidates, settings.render_dir, doc.id)
            for page in db.execute(select(DocumentPage).where(
                    DocumentPage.document_id == doc.id)).scalars():
                if page.page_number in rendered:
                    page.rendered_png = rendered[page.page_number]
                    page.dpi = 200
            db.commit()

        items_created = _upsert_line_items(db, doc, ext)

        verification = {"attempted": False, "verified": 0, "errors": []}
        cfg = get_ai_config(db)
        provider = get_provider(db)
        if provider and cfg.get("visual_verification") and rendered:
            _set_status(db, doc, "ai_verifying")
            verification = _run_visual_verification(db, doc, ext, rendered, provider)

        _set_status(db, doc, "reconciling")
        recon = reconcile_document(db, doc)

        _set_status(db, doc, "awaiting_review")
        db.add(AuditLog(case_id=doc.case_id, action="document_processed",
                        detail={"document_id": doc.id, "items": items_created,
                                "verification": verification["attempted"],
                                "needs_review": recon["needs_review"]}))
        db.commit()
        return {"status": "awaiting_review", "items": items_created,
                "verification": verification, "reconciliation": recon}
    except Exception as e:  # keep app alive; surface error on the document
        log.exception("Document pipeline failed")
        _set_status(db, doc, "failed", str(e))
        raise


def _store_pages_and_raw(db: Session, doc: Document, ext: PdfExtraction) -> None:
    for p in ext.pages:
        db.add(DocumentPage(document_id=doc.id, page_number=p.page_number,
                            statement_type=p.statement_type,
                            is_candidate=1 if p.is_candidate else 0,
                            text_chars=p.text_chars))
    for it in ext.items:
        db.add(ExtractionResult(
            document_id=doc.id, case_id=doc.case_id, page_number=it.page_number,
            statement_type=it.statement_type, label=it.label,
            period_label=it.period_label or doc.fiscal_year_label,
            raw_value=it.raw_value, normalised_value=it.value,
            canonical_metric=it.metric or "", extraction_method=it.method,
        ))
    db.commit()


def _upsert_line_items(db: Session, doc: Document, ext: PdfExtraction) -> int:
    """Map extracted items to canonical line items (python_value side)."""
    created = 0
    period_fallback = doc.fiscal_year_label
    seen: set[tuple[str, str]] = set()
    for it in ext.items:
        if not it.metric or it.value is None:
            continue
        period = it.period_label or period_fallback
        if not period:
            continue
        key = (period, it.metric)
        if key in seen:
            continue  # first match on a page wins (statements list totals later)
        seen.add(key)

        _ensure_period(db, doc.case_id, period)
        row = db.execute(select(FinancialLineItem).where(
            FinancialLineItem.case_id == doc.case_id,
            FinancialLineItem.period_label == period,
            FinancialLineItem.metric == it.metric,
        )).scalars().first()
        if row is None:
            row = FinancialLineItem(
                case_id=doc.case_id, period_label=period,
                statement=STATEMENT_OF.get(it.metric, "pnl"), metric=it.metric,
            )
            db.add(row)
            created += 1
        row.python_value = it.value
        row.original_label = it.label
        row.original_display = it.raw_value
        row.unit = ext.unit_name or "INR"
        row.source_document_id = doc.id
        row.source_page = it.page_number
        if row.verification_status == "unverified":
            row.confidence = 0.6
    db.commit()
    return created


def _ensure_period(db: Session, case_id: str, label: str) -> None:
    existing = db.execute(select(FinancialPeriod).where(
        FinancialPeriod.case_id == case_id, FinancialPeriod.label == label,
    )).scalars().first()
    if existing:
        return
    all_periods = db.execute(select(FinancialPeriod).where(
        FinancialPeriod.case_id == case_id)).scalars().all()
    db.add(FinancialPeriod(case_id=case_id, label=label, order_index=len(all_periods)))
    db.flush()
    # keep chronological order by label
    rows = sorted(db.execute(select(FinancialPeriod).where(
        FinancialPeriod.case_id == case_id)).scalars().all(), key=lambda r: r.label)
    for i, r in enumerate(rows):
        r.order_index = i


def _run_visual_verification(db: Session, doc: Document, ext: PdfExtraction,
                             rendered: dict[int, str], provider) -> dict[str, Any]:
    """Send each rendered candidate page + python values to Gemini for checking."""
    out = {"attempted": True, "verified": 0, "errors": []}
    by_page: dict[int, list] = {}
    for it in ext.items:
        if it.metric and it.value is not None and it.page_number in rendered:
            by_page.setdefault(it.page_number, []).append(it)

    for page_no, items in by_page.items():
        payload = [{"metric": i.metric, "label": i.label,
                    "period": i.period_label or doc.fiscal_year_label,
                    "python_value": i.value} for i in items[:20]]
        try:
            result = logged_call(
                db, "verify_document", doc.case_id,
                provider.verify_document, rendered[page_no], page_no,
                items[0].statement_type, ext.unit_name, ext.unit_multiplier,
                ext.period_labels, payload, _provider=provider,
            )
        except AIProviderError as e:
            out["errors"].append(str(e))
            continue
        for item in result.get("items", []):
            metric = item.get("metric", "")
            db.add(VerificationResult(
                case_id=doc.case_id, document_id=doc.id, page_number=page_no,
                metric=metric, period_label=str(item.get("period", "")),
                label_seen=str(item.get("label_seen", ""))[:300],
                python_value=item.get("python_value"),
                visual_value=item.get("visual_value"),
                status=item.get("status", "not_visible"),
                confidence=float(item.get("confidence", 0) or 0),
                raw_response=item,
            ))
            if item.get("status") == "verified":
                out["verified"] += 1
        db.commit()
    return out


def reconcile_document(db: Session, doc: Document) -> dict[str, Any]:
    """Compare Python values against AI visual values → verification status."""
    items = db.execute(select(FinancialLineItem).where(
        FinancialLineItem.case_id == doc.case_id,
        FinancialLineItem.source_document_id == doc.id,
    )).scalars().all()
    verifs = db.execute(select(VerificationResult).where(
        VerificationResult.document_id == doc.id)).scalars().all()
    v_by_metric: dict[str, VerificationResult] = {}
    for v in verifs:
        cur = v_by_metric.get(v.metric)
        if cur is None or v.confidence > cur.confidence:
            v_by_metric[v.metric] = v

    counts = {"verified": 0, "needs_review": 0, "low_confidence": 0, "unverified": 0}
    for it in items:
        v = v_by_metric.get(it.metric)
        if v is None:
            it.verification_status = "unverified"
            counts["unverified"] += 1
            continue
        it.ai_visual_value = v.visual_value
        it.confidence = v.confidence
        if v.status == "verified" and v.visual_value is not None and it.python_value:
            diff = abs(v.visual_value - it.python_value) / max(abs(it.python_value), 1)
            if diff <= MATCH_TOLERANCE:
                it.verification_status = "verified"
                counts["verified"] += 1
            else:
                it.verification_status = "needs_review"
                counts["needs_review"] += 1
        elif v.status == "difference":
            it.verification_status = "needs_review"
            counts["needs_review"] += 1
        else:  # not_visible / ambiguous
            it.verification_status = "low_confidence"
            counts["low_confidence"] += 1
    db.commit()
    return {**counts, "needs_review": counts["needs_review"]}
