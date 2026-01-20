"""
FastAPI Application for Claro Invoice Automation Demo
Simplified flow: Email → ZIP (XML only) → Process → Validate → SAP
With optional OCR demo for Orden de Compra and Formato de Cumplimiento
"""
import logging
import os
import json
import base64
from datetime import datetime
from typing import Optional, List, Dict, Any

# Load environment variables from local.settings.json (Azure Functions format)
def load_local_settings():
    """Load environment variables from local.settings.json - try multiple paths"""
    print("=" * 60)
    print("[STARTUP] Attempting to load local.settings.json")
    print(f"[STARTUP] __file__ = {__file__}")
    print(f"[STARTUP] cwd = {os.getcwd()}")

    # Try multiple possible paths
    possible_paths = [
        os.path.join(os.path.dirname(__file__), 'local.settings.json'),
        os.path.join(os.getcwd(), 'local.settings.json'),
        'local.settings.json',
        r'C:\Claro\backend\local.settings.json',  # Hardcoded fallback for Windows
    ]

    settings_path = None
    for path in possible_paths:
        print(f"[STARTUP] Trying: {path} -> exists: {os.path.exists(path)}")
        if os.path.exists(path):
            settings_path = path
            break

    if settings_path:
        try:
            with open(settings_path, 'r') as f:
                settings = json.load(f)
                values = settings.get('Values', {})
                loaded = 0
                already_set = 0
                for key, value in values.items():
                    if key not in os.environ:
                        os.environ[key] = str(value)
                        loaded += 1
                    else:
                        already_set += 1
                print(f"[STARTUP] Loaded {loaded} new, {already_set} already set")
        except Exception as e:
            print(f"[STARTUP] Error loading settings: {e}")
    else:
        print("[STARTUP] WARNING: No local.settings.json found!")

    # Always show current state of key env vars
    di_endpoint = os.getenv('AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT')
    di_key = os.getenv('AZURE_DOCUMENT_INTELLIGENCE_KEY')
    di_model = os.getenv('AZURE_DOC_INTEL_MODEL_ORDEN_COMPRA')
    print(f"[STARTUP] FINAL STATE:")
    print(f"  DOC_INTEL_ENDPOINT: {di_endpoint[:40] + '...' if di_endpoint else 'NOT SET'}")
    print(f"  DOC_INTEL_KEY: {'SET (' + '*'*6 + di_key[-4:] + ')' if di_key else 'NOT SET'}")
    print(f"  MODEL_OC: {di_model if di_model else 'NOT SET'}")
    print("=" * 60)

load_local_settings()

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from models import (
    ApiResponse, InvoicePackage, InvoiceListItem, ProcessingStep,
    ChatRequest, ChatResponse, ValidationRule, NotifyRequest,
    DocumentType, ProcessedDocument, AttachmentInfo, FieldComparison,
    OCComparisonResult, XML_TO_OC_FIELD_MAPPING
)
from blob_service import get_blob_service
from xml_parser import parse_dian_xml, xml_to_dict
from document_processor import get_document_processor, OCRDocumentType
from excel_processor import get_excel_processor
from openai_service import get_openai_service
from rules_engine import get_rules_engine

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Claro Invoice Automation API",
    description="API para automatización de facturas electrónicas DIAN",
    version="2.0.0",
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for processed invoices (demo purposes)
invoice_cache: Dict[str, InvoicePackage] = {}
custom_rules_cache: Dict[str, List[ValidationRule]] = {}
uploaded_documents_cache: Dict[str, List[ProcessedDocument]] = {}
excel_data_cache: Dict[str, Dict[str, Any]] = {}  # Cache for Excel demo data
attachments_cache: Dict[str, List[Dict[str, Any]]] = {}  # Cache for nested ZIP attachments
oc_comparison_cache: Dict[str, OCComparisonResult] = {}  # Cache for OC comparison results


# Request/Response models
class ProcessRequest(BaseModel):
    """Request to process invoice"""
    force_reprocess: bool = False


class SubmitRequest(BaseModel):
    """Request to submit to SAP"""
    selected_fields: List[str] = []
    notes: Optional[str] = None
    force: bool = False  # Allow submission even with validation failures


class CustomRuleRequest(BaseModel):
    """Request to add custom rule"""
    rule: ValidationRule


class UploadDocumentRequest(BaseModel):
    """Request to upload a document for OCR"""
    document_type: str  # "orden_compra" or "formato_cumplimiento"
    file_content: str  # Base64 encoded
    file_name: str


# Health check
@app.get("/api/health")
async def health_check():
    """Health check endpoint with OCR diagnostic info"""
    doc_processor = get_document_processor()
    models_info = doc_processor.get_available_models()

    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "ocr_status": "REAL OCR" if not models_info.get("mock_mode") else "MOCK MODE",
        "ocr_models": models_info,
        "env_check": {
            "AZURE_DOC_INTEL_ENDPOINT": bool(os.getenv('AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT')),
            "AZURE_DOC_INTEL_KEY": bool(os.getenv('AZURE_DOCUMENT_INTELLIGENCE_KEY')),
            "MODEL_OC": os.getenv('AZURE_DOC_INTEL_MODEL_ORDEN_COMPRA', 'not set'),
            "MODEL_CUMPLIMIENTO": os.getenv('AZURE_DOC_INTEL_MODEL_CUMPLIMIENTO', 'not set'),
        },
    }


# Invoice list
@app.get("/api/invoices", response_model=ApiResponse)
async def list_invoices():
    """List available invoices in blob storage"""
    try:
        blob_service = get_blob_service()
        invoices = blob_service.list_invoices()

        items = []
        for inv in invoices:
            # Check if already processed
            cached = invoice_cache.get(inv["invoice_id"])

            item = InvoiceListItem(
                invoice_id=inv["invoice_id"],
                invoice_number=cached.xml_data.invoice_number if cached and cached.xml_data else None,
                supplier_name=cached.xml_data.supplier.registration_name if cached and cached.xml_data else None,
                total_amount=cached.xml_data.monetary_total.payable_amount if cached and cached.xml_data else None,
                issue_date=cached.xml_data.issue_date if cached and cached.xml_data else None,
                status=cached.status if cached else "pending",
                received_at=inv["last_modified"],
            )
            items.append(item)

        return ApiResponse(
            success=True,
            message=f"Found {len(items)} invoices",
            data=[item.model_dump() for item in items],
        )
    except Exception as e:
        logger.error(f"Error listing invoices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Delete invoice
@app.delete("/api/invoices/{invoice_id}", response_model=ApiResponse)
async def delete_invoice(invoice_id: str):
    """Delete an invoice from blob storage and cache"""
    try:
        blob_service = get_blob_service()

        # Try to delete from blob storage
        deleted_from_blob = False
        try:
            deleted_from_blob = blob_service.delete_invoice(invoice_id)
        except Exception as e:
            logger.warning(f"Could not delete from blob storage: {e}")

        # Remove from caches
        deleted_from_cache = False
        if invoice_id in invoice_cache:
            del invoice_cache[invoice_id]
            deleted_from_cache = True

        if invoice_id in custom_rules_cache:
            del custom_rules_cache[invoice_id]

        if invoice_id in uploaded_documents_cache:
            del uploaded_documents_cache[invoice_id]

        if invoice_id in excel_data_cache:
            del excel_data_cache[invoice_id]

        if not deleted_from_blob and not deleted_from_cache:
            raise HTTPException(status_code=404, detail="Invoice not found")

        return ApiResponse(
            success=True,
            message=f"Invoice {invoice_id} deleted successfully",
            data={
                "invoice_id": invoice_id,
                "deleted_from_blob": deleted_from_blob,
                "deleted_from_cache": deleted_from_cache,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting invoice {invoice_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Get invoice details
@app.get("/api/invoices/{invoice_id}", response_model=ApiResponse)
async def get_invoice(invoice_id: str):
    """Get invoice details"""
    try:
        if invoice_id not in invoice_cache:
            # Return basic info
            blob_service = get_blob_service()
            invoices = blob_service.list_invoices()
            inv = next((i for i in invoices if i["invoice_id"] == invoice_id), None)

            if not inv:
                raise HTTPException(status_code=404, detail="Invoice not found")

            return ApiResponse(
                success=True,
                message="Invoice found but not processed",
                data={
                    "invoice_id": invoice_id,
                    "status": "pending",
                    "blob_path": inv["path"],
                },
            )

        package = invoice_cache[invoice_id]
        return ApiResponse(
            success=True,
            message="Invoice retrieved",
            data=_serialize_package(package),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting invoice {invoice_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Process invoice (simplified - only XML from ZIP)
@app.post("/api/invoices/{invoice_id}/process", response_model=ApiResponse)
async def process_invoice(invoice_id: str, request: ProcessRequest = ProcessRequest()):
    """Process invoice - extract ZIP and parse XML only"""
    try:
        # Check if already processed
        if invoice_id in invoice_cache and not request.force_reprocess:
            return ApiResponse(
                success=True,
                message="Invoice already processed",
                data=_serialize_package(invoice_cache[invoice_id]),
            )

        blob_service = get_blob_service()

        # Find invoice
        invoices = blob_service.list_invoices()
        inv = next((i for i in invoices if i["invoice_id"] == invoice_id), None)

        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")

        # Create package
        package = InvoicePackage(
            invoice_id=invoice_id,
            blob_path=inv["path"],
            received_at=inv["last_modified"],
            status="processing",
            processing_steps=[],
        )

        # Step 1: Receive from email (already done by Logic App)
        step1 = ProcessingStep(
            step_number=1,
            name="Recepción Email",
            status="completed",
            started_at=inv["last_modified"],
            completed_at=inv["last_modified"],
            message="ZIP recibido vía Logic App",
        )
        package.processing_steps.append(step1)

        # Step 2: Extract ZIP
        step2 = ProcessingStep(
            step_number=2,
            name="Extracción ZIP",
            status="in_progress",
            started_at=datetime.now(),
        )
        package.processing_steps.append(step2)

        try:
            extracted_files = blob_service.extract_zip(inv["path"])
            categories = blob_service.categorize_extracted_files(extracted_files)
            step2.status = "completed"
            step2.completed_at = datetime.now()
            step2.message = f"Extraídos {len(extracted_files)} archivos"
        except Exception as e:
            step2.status = "error"
            step2.message = str(e)
            raise

        # Step 3: Parse XML DIAN
        step3 = ProcessingStep(
            step_number=3,
            name="Parseo XML DIAN",
            status="in_progress",
            started_at=datetime.now(),
        )
        package.processing_steps.append(step3)

        xml_files = categories.get('xml', [])
        if xml_files:
            try:
                xml_content = extracted_files[xml_files[0]].decode('utf-8')
                package.xml_data = parse_dian_xml(xml_content, xml_files[0])
                step3.status = "completed"
                step3.completed_at = datetime.now()
                step3.message = f"Factura {package.xml_data.invoice_number} parseada correctamente"
            except Exception as e:
                step3.status = "error"
                step3.message = str(e)
                logger.error(f"Error parsing XML: {e}")
        else:
            step3.status = "error"
            step3.message = "No se encontró archivo XML en el ZIP"

        # Step 4: Ready for validation
        step4 = ProcessingStep(
            step_number=4,
            name="Listo para Validación",
            status="completed",
            started_at=datetime.now(),
            completed_at=datetime.now(),
            message="Datos extraídos, puede proceder con validación",
        )
        package.processing_steps.append(step4)

        package.status = "processed" if step3.status == "completed" else "error"

        # Cache the processed package
        invoice_cache[invoice_id] = package

        # Initialize empty uploaded documents for this invoice
        if invoice_id not in uploaded_documents_cache:
            uploaded_documents_cache[invoice_id] = []

        return ApiResponse(
            success=True,
            message="Invoice processed successfully" if package.status == "processed" else "Processing completed with errors",
            data=_serialize_package(package),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing invoice {invoice_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Get XML data
@app.get("/api/invoices/{invoice_id}/xml", response_model=ApiResponse)
async def get_xml_data(invoice_id: str):
    """Get parsed XML data"""
    if invoice_id not in invoice_cache:
        raise HTTPException(status_code=404, detail="Invoice not processed")

    package = invoice_cache[invoice_id]
    if not package.xml_data:
        raise HTTPException(status_code=404, detail="No XML data available")

    flat_data = xml_to_dict(package.xml_data)

    # Get uploaded documents to check which references were satisfied
    uploaded_docs = uploaded_documents_cache.get(invoice_id, [])
    has_oc = any(d.document_type == DocumentType.ORDEN_COMPRA for d in uploaded_docs)

    # Update attachment references with found status
    attachment_refs = []
    for ref in package.xml_data.attachment_references:
        ref_dict = {
            "reference_id": ref.reference_id,
            "reference_type": ref.reference_type,
            "description": ref.description,
            "found_in_zip": False,
            "matched_filename": None,
        }
        # Check if this reference was satisfied by an uploaded document
        if ref.reference_type == "orden_compra" and has_oc:
            ref_dict["found_in_zip"] = True
            oc_doc = next((d for d in uploaded_docs if d.document_type == DocumentType.ORDEN_COMPRA), None)
            if oc_doc:
                ref_dict["matched_filename"] = oc_doc.file_name
        attachment_refs.append(ref_dict)

    return ApiResponse(
        success=True,
        message="XML data retrieved",
        data={
            "invoice_number": package.xml_data.invoice_number,
            "cufe": package.xml_data.cufe,
            "issue_date": package.xml_data.issue_date.isoformat() if package.xml_data.issue_date else None,
            "issue_time": package.xml_data.issue_time,
            "due_date": package.xml_data.due_date.isoformat() if package.xml_data.due_date else None,
            "currency_code": package.xml_data.currency_code,
            "invoice_type_code": package.xml_data.invoice_type_code,
            "flat_data": flat_data,
            "supplier": package.xml_data.supplier.model_dump(),
            "customer": package.xml_data.customer.model_dump(),
            "taxes": [t.model_dump() for t in package.xml_data.taxes],
            "withholding_taxes": [t.model_dump() for t in package.xml_data.withholding_taxes],
            "monetary_total": package.xml_data.monetary_total.model_dump(),
            "total_iva": package.xml_data.total_iva,
            "total_retenciones": package.xml_data.total_retenciones,
            "lines": [l.model_dump() for l in package.xml_data.lines],
            "line_count": package.xml_data.line_count,
            "order_reference": package.xml_data.order_reference.model_dump() if package.xml_data.order_reference else None,
            "attachment_references": attachment_refs,
            "notes": package.xml_data.notes,
            "qr_code": package.xml_data.qr_code,
        },
    )


# Upload PDF for OCR processing (NEW ENDPOINT)
@app.post("/api/invoices/{invoice_id}/upload-document", response_model=ApiResponse)
async def upload_document_for_ocr(
    invoice_id: str,
    file: UploadFile = File(...),
    document_type: str = Form(...)
):
    """
    Upload a PDF document for OCR processing

    Args:
        invoice_id: Invoice to associate the document with
        file: PDF file to process
        document_type: Type of document ("orden_compra" or "formato_cumplimiento")
    """
    if invoice_id not in invoice_cache:
        raise HTTPException(status_code=404, detail="Invoice not processed. Process the invoice first.")

    if document_type not in ["orden_compra", "formato_cumplimiento"]:
        raise HTTPException(
            status_code=400,
            detail="document_type must be 'orden_compra' or 'formato_cumplimiento'"
        )

    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        # Read file content
        content = await file.read()

        # Get document processor
        doc_processor = get_document_processor()

        # Map to OCR type
        ocr_type = (
            OCRDocumentType.ORDEN_COMPRA
            if document_type == "orden_compra"
            else OCRDocumentType.FORMATO_CUMPLIMIENTO
        )

        # Process with appropriate model
        processed_doc = doc_processor.process_pdf(
            content=content,
            filename=file.filename,
            doc_type=ocr_type
        )

        # Store in cache
        if invoice_id not in uploaded_documents_cache:
            uploaded_documents_cache[invoice_id] = []

        # Remove any existing document of the same type
        uploaded_documents_cache[invoice_id] = [
            d for d in uploaded_documents_cache[invoice_id]
            if d.document_type.value != document_type
        ]

        # Add new document
        uploaded_documents_cache[invoice_id].append(processed_doc)

        # Return processed document info
        return ApiResponse(
            success=True,
            message=f"Document processed successfully with {len(processed_doc.extracted_fields)} fields extracted",
            data=doc_processor.get_document_summary(processed_doc),
        )

    except Exception as e:
        logger.error(f"Error processing uploaded document: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Get uploaded documents for an invoice
@app.get("/api/invoices/{invoice_id}/documents", response_model=ApiResponse)
async def get_uploaded_documents(invoice_id: str):
    """Get uploaded and processed documents for an invoice"""
    if invoice_id not in invoice_cache:
        raise HTTPException(status_code=404, detail="Invoice not processed")

    documents = uploaded_documents_cache.get(invoice_id, [])
    doc_processor = get_document_processor()

    return ApiResponse(
        success=True,
        message=f"Retrieved {len(documents)} documents",
        data=[doc_processor.get_document_summary(doc) for doc in documents],
    )


# Delete uploaded document
@app.delete("/api/invoices/{invoice_id}/documents/{document_type}", response_model=ApiResponse)
async def delete_uploaded_document(invoice_id: str, document_type: str):
    """Delete an uploaded document"""
    if invoice_id not in uploaded_documents_cache:
        raise HTTPException(status_code=404, detail="No documents found")

    original_count = len(uploaded_documents_cache[invoice_id])
    uploaded_documents_cache[invoice_id] = [
        d for d in uploaded_documents_cache[invoice_id]
        if d.document_type.value != document_type
    ]

    if len(uploaded_documents_cache[invoice_id]) == original_count:
        raise HTTPException(status_code=404, detail="Document not found")

    return ApiResponse(
        success=True,
        message=f"Document {document_type} deleted",
    )


# ============ NESTED ZIP ATTACHMENTS & OC COMPARISON ============

# Local ZIP directory for demo
LOCAL_ZIP_DIR = "/mnt/c/Claro"

# Get attachments from ZIP (all PDFs)
@app.get("/api/invoices/{invoice_id}/attachments", response_model=ApiResponse)
async def get_attachments(invoice_id: str):
    """
    Get list of ALL PDF attachments from the invoice ZIP.
    Includes PDFs from main ZIP and nested ZIP (Anexo.zip).
    Falls back to local disk for demo purposes.
    """
    if invoice_id not in invoice_cache:
        raise HTTPException(status_code=404, detail="Invoice not processed. Process the invoice first.")

    # Check if attachments already extracted
    if invoice_id in attachments_cache and len(attachments_cache[invoice_id]) > 0:
        attachments = attachments_cache[invoice_id]
        # Check which are processed
        uploaded_docs = uploaded_documents_cache.get(invoice_id, [])
        oc_doc = next((d for d in uploaded_docs if d.document_type == DocumentType.ORDEN_COMPRA), None)

        attachment_infos = []
        for att in attachments:
            info = AttachmentInfo(
                name=att["name"],
                size=att["size"],
                is_processed=oc_doc is not None and oc_doc.file_name == att["name"],
                document_type="orden_compra" if oc_doc and oc_doc.file_name == att["name"] else None,
                source=att.get("source", "main_zip")
            )
            attachment_infos.append(info.model_dump())

        return ApiResponse(
            success=True,
            message=f"Encontrados {len(attachment_infos)} archivos PDF disponibles",
            data={
                "attachments": attachment_infos,
                "nested_zip_name": None,
            },
        )

    # Need to extract ZIP to find attachments
    package = invoice_cache[invoice_id]
    blob_service = get_blob_service()
    extracted = None

    # Try blob storage first
    try:
        extracted = blob_service.extract_zip_from_blob_with_nested(package.blob_path)
        logger.info(f"Extracted from blob storage: {len(extracted.get('attachments', []))} PDFs")
    except Exception as e:
        logger.warning(f"Could not extract from blob storage for {invoice_id}: {e}")

    # If no attachments from blob, try local file for demo
    if not extracted or len(extracted.get("attachments", [])) == 0:
        local_zip_path = os.path.join(LOCAL_ZIP_DIR, f"{invoice_id}.zip")
        if os.path.exists(local_zip_path):
            try:
                extracted = blob_service.extract_zip_from_local_file(local_zip_path)
                logger.info(f"Extracted from local file {local_zip_path}: {len(extracted.get('attachments', []))} PDFs")
            except Exception as e:
                logger.warning(f"Could not extract from local file {local_zip_path}: {e}")

    if extracted and len(extracted.get("attachments", [])) > 0:
        attachments_cache[invoice_id] = extracted.get("attachments", [])

        attachment_infos = [
            AttachmentInfo(
                name=att["name"],
                size=att["size"],
                is_processed=False,
                source=att.get("source", "zip")
            ).model_dump()
            for att in extracted.get("attachments", [])
        ]

        return ApiResponse(
            success=True,
            message=f"Encontrados {len(attachment_infos)} archivos PDF en el ZIP",
            data={
                "attachments": attachment_infos,
                "nested_zip_name": extracted.get("nested_zip_name"),
            },
        )

    # No attachments found
    attachments_cache[invoice_id] = []
    return ApiResponse(
        success=True,
        message="No se encontraron archivos PDF en el ZIP",
        data={
            "attachments": [],
            "nested_zip_name": None,
        },
    )


# Process attachment with OCR
@app.post("/api/invoices/{invoice_id}/process-attachment", response_model=ApiResponse)
async def process_attachment(
    invoice_id: str,
    attachment_name: str = Query(..., description="Name of attachment file to process"),
    document_type: str = Query(default="orden_compra", description="Document type: orden_compra or formato_cumplimiento")
):
    """
    Process a PDF attachment from nested ZIP with Document Intelligence OCR.
    """
    if invoice_id not in invoice_cache:
        raise HTTPException(status_code=404, detail="Invoice not processed")

    if document_type not in ["orden_compra", "formato_cumplimiento"]:
        raise HTTPException(status_code=400, detail="document_type must be 'orden_compra' or 'formato_cumplimiento'")

    # Get attachment content
    if invoice_id not in attachments_cache:
        raise HTTPException(status_code=404, detail="No attachments available. Call GET /attachments first.")

    attachment = next(
        (a for a in attachments_cache[invoice_id] if a["name"] == attachment_name),
        None
    )

    if not attachment:
        raise HTTPException(status_code=404, detail=f"Attachment '{attachment_name}' not found")

    try:
        # Get document processor
        doc_processor = get_document_processor()

        # Map to OCR type
        ocr_type = (
            OCRDocumentType.ORDEN_COMPRA
            if document_type == "orden_compra"
            else OCRDocumentType.FORMATO_CUMPLIMIENTO
        )

        # Process with OCR
        processed_doc = doc_processor.process_pdf(
            content=attachment["content"],
            filename=attachment_name,
            doc_type=ocr_type
        )

        # Store in uploaded documents cache
        if invoice_id not in uploaded_documents_cache:
            uploaded_documents_cache[invoice_id] = []

        # Remove existing document of same type
        uploaded_documents_cache[invoice_id] = [
            d for d in uploaded_documents_cache[invoice_id]
            if d.document_type.value != document_type
        ]

        uploaded_documents_cache[invoice_id].append(processed_doc)

        # If this is an OC, run comparison
        if document_type == "orden_compra":
            _run_oc_comparison(invoice_id, processed_doc)

        return ApiResponse(
            success=True,
            message=f"Attachment processed with {len(processed_doc.extracted_fields)} fields extracted",
            data=doc_processor.get_document_summary(processed_doc),
        )

    except Exception as e:
        logger.error(f"Error processing attachment {attachment_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _run_oc_comparison(invoice_id: str, oc_doc: ProcessedDocument):
    """Run comparison between XML and processed OC document"""
    if invoice_id not in invoice_cache:
        return

    package = invoice_cache[invoice_id]
    if not package.xml_data:
        return

    # Build comparison
    comparisons = []
    xml_data = package.xml_data

    # Get XML values
    xml_values = {
        "order_reference": xml_data.order_reference.order_id if xml_data.order_reference else None,
        "supplier_nit": xml_data.supplier.company_id,
        "supplier_name": xml_data.supplier.registration_name,
        "total_payable": xml_data.monetary_total.payable_amount,
        "line_extension_amount": xml_data.monetary_total.line_extension_amount,
        "total_iva": xml_data.total_iva,
    }

    # Get OC values from extracted fields
    oc_fields = oc_doc.extracted_fields

    for xml_field, mapping in XML_TO_OC_FIELD_MAPPING.items():
        xml_val = xml_values.get(xml_field)
        oc_field_name = mapping["oc_field"]
        oc_extracted = oc_fields.get(oc_field_name)
        oc_val = oc_extracted.value if oc_extracted else None

        match, match_type, notes = _compare_values(
            xml_val, oc_val, mapping.get("compare_type", "exact"),
            mapping.get("tolerance", 0.05)
        )

        comparisons.append(FieldComparison(
            field_name=xml_field,
            field_label=mapping["label"],
            xml_value=xml_val,
            oc_value=oc_val,
            match=match,
            match_type=match_type,
            notes=notes
        ))

    # Calculate overall result
    matched = sum(1 for c in comparisons if c.match)
    total = len(comparisons)
    percentage = (matched / total) * 100 if total > 0 else 0

    # Determine conclusion
    if percentage >= 90:
        conclusion = f"Los datos de la factura XML coinciden con la Orden de Compra. {matched} de {total} campos verificados correctamente."
        conclusion_type = "success"
    elif percentage >= 70:
        conclusion = f"La mayoría de datos coinciden ({matched}/{total}), pero hay algunas discrepancias que revisar."
        conclusion_type = "warning"
    else:
        conclusion = f"Se encontraron discrepancias significativas entre el XML y la OC. Solo {matched} de {total} campos coinciden."
        conclusion_type = "error"

    result = OCComparisonResult(
        invoice_id=invoice_id,
        xml_oc_reference=xml_values.get("order_reference") or "N/A",
        oc_document_number=oc_fields.get("PurchaseNumber", {}).value if oc_fields.get("PurchaseNumber") else None,
        oc_file_name=oc_doc.file_name,
        comparisons=comparisons,
        overall_match=percentage >= 80,
        match_percentage=percentage,
        matched_fields=matched,
        total_fields=total,
        conclusion=conclusion,
        conclusion_type=conclusion_type
    )

    oc_comparison_cache[invoice_id] = result


def _parse_colombian_number(value) -> float:
    """
    Parse a Colombian formatted number.
    Colombian format: . = thousands separator, , = decimal separator
    Examples: "137.310.992" -> 137310992, "1.234,56" -> 1234.56
    """
    s = str(value).strip()
    # Remove currency symbols and text
    s = s.replace('$', '').replace('COP', '').replace(' ', '').strip()

    # Check if it has both . and , to determine format
    has_dot = '.' in s
    has_comma = ',' in s

    if has_dot and has_comma:
        # Colombian format: 1.234.567,89 -> dots are thousands, comma is decimal
        s = s.replace('.', '')  # Remove thousand separators
        s = s.replace(',', '.')  # Convert decimal separator
    elif has_dot:
        # Could be Colombian (137.310.992) or decimal (123.45)
        # Count dots - if more than one, it's thousand separator
        dot_count = s.count('.')
        if dot_count > 1:
            # Multiple dots = thousand separators (Colombian)
            s = s.replace('.', '')
        else:
            # Single dot - check position to determine if thousands or decimal
            # If 3 digits after dot and total > 6 chars, likely thousands
            parts = s.split('.')
            if len(parts) == 2 and len(parts[1]) == 3 and len(s) > 6:
                s = s.replace('.', '')  # Thousand separator
            # else keep as decimal
    elif has_comma:
        # Comma only - likely decimal separator
        s = s.replace(',', '.')

    return float(s)


def _compare_values(xml_val, oc_val, compare_type: str, tolerance: float = 0.05):
    """Compare two values based on comparison type"""
    # Handle missing values
    if xml_val is None and oc_val is None:
        return True, "both_missing", "Ambos valores están vacíos"
    if xml_val is None:
        return False, "missing_xml", "Valor no encontrado en XML"
    if oc_val is None:
        return False, "missing_oc", "Valor no extraído de la OC"

    # Convert to strings for comparison
    xml_str = str(xml_val).strip().lower()
    oc_str = str(oc_val).strip().lower()

    if compare_type == "exact":
        # Exact string match (case-insensitive)
        if xml_str == oc_str:
            return True, "exact", None
        # Check if one contains the other (for partial matches)
        if xml_str in oc_str or oc_str in xml_str:
            return True, "partial", "Coincidencia parcial"
        return False, "mismatch", f"XML: {xml_val}, OC: {oc_val}"

    elif compare_type == "contains":
        # Check if one contains the other (useful for names)
        if xml_str in oc_str or oc_str in xml_str:
            return True, "exact", None  # Changed from "partial" to "exact" for exact substring match
        # Check word overlap
        xml_words = set(xml_str.split())
        oc_words = set(oc_str.split())
        overlap = xml_words & oc_words
        if len(overlap) >= min(len(xml_words), len(oc_words)) * 0.5:
            return True, "partial", "Coincidencia parcial de palabras"
        return False, "mismatch", f"XML: {xml_val}, OC: {oc_val}"

    elif compare_type == "numeric":
        # Numeric comparison with tolerance (handles Colombian number format)
        try:
            # Parse Colombian formatted numbers
            xml_num = _parse_colombian_number(xml_val)
            oc_num = _parse_colombian_number(oc_val)

            if xml_num == oc_num:
                return True, "exact", None

            # Check within tolerance
            if xml_num != 0:
                diff_pct = abs(xml_num - oc_num) / abs(xml_num)
                if diff_pct <= tolerance:
                    return True, "numeric_close", f"Diferencia: {diff_pct*100:.1f}%"

            return False, "mismatch", f"XML: ${xml_num:,.0f}, OC: ${oc_num:,.0f}"
        except (ValueError, TypeError):
            # Fall back to string comparison
            if xml_str == oc_str:
                return True, "exact", None
            return False, "mismatch", "No se pudo comparar numéricamente"

    return False, "unknown", "Tipo de comparación desconocido"


# Get OC comparison result
@app.get("/api/invoices/{invoice_id}/oc-comparison", response_model=ApiResponse)
async def get_oc_comparison(invoice_id: str):
    """
    Get the comparison result between XML and Orden de Compra.
    Must process an OC attachment first.
    """
    if invoice_id not in invoice_cache:
        raise HTTPException(status_code=404, detail="Invoice not processed")

    if invoice_id not in oc_comparison_cache:
        # Check if OC document exists
        uploaded_docs = uploaded_documents_cache.get(invoice_id, [])
        oc_doc = next((d for d in uploaded_docs if d.document_type == DocumentType.ORDEN_COMPRA), None)

        if not oc_doc:
            raise HTTPException(
                status_code=404,
                detail="No OC document processed. Process an attachment first."
            )

        # Run comparison
        _run_oc_comparison(invoice_id, oc_doc)

    result = oc_comparison_cache.get(invoice_id)
    if not result:
        raise HTTPException(status_code=404, detail="Comparison not available")

    return ApiResponse(
        success=True,
        message=f"Comparison complete: {result.match_percentage:.0f}% match",
        data={
            "invoice_id": result.invoice_id,
            "xml_oc_reference": result.xml_oc_reference,
            "oc_document_number": result.oc_document_number,
            "oc_file_name": result.oc_file_name,
            "comparisons": [c.model_dump() for c in result.comparisons],
            "overall_match": result.overall_match,
            "match_percentage": result.match_percentage,
            "matched_fields": result.matched_fields,
            "total_fields": result.total_fields,
            "conclusion": result.conclusion,
            "conclusion_type": result.conclusion_type,
        },
    )


# Validate invoice
@app.post("/api/invoices/{invoice_id}/validate", response_model=ApiResponse)
async def validate_invoice(invoice_id: str):
    """Run validation rules on invoice - auto-processes if needed"""
    package = None
    xml_data = None

    # Try to get from cache first
    if invoice_id in invoice_cache:
        package = invoice_cache[invoice_id]
        xml_data = package.xml_data
    else:
        # Not in cache - try to load from blob storage on-the-fly
        try:
            blob_service = get_blob_service()

            # Find the invoice in blob storage
            invoices = blob_service.list_invoices()
            inv = next((i for i in invoices if i["invoice_id"] == invoice_id), None)

            if inv and inv.get("path"):
                # Extract ZIP and parse XML
                extracted_files = blob_service.extract_zip(inv["path"])
                categories = blob_service.categorize_extracted_files(extracted_files)

                xml_files = categories.get('xml', [])
                if xml_files:
                    xml_content = extracted_files[xml_files[0]].decode('utf-8')
                    xml_data = parse_dian_xml(xml_content, xml_files[0])
        except Exception as e:
            logger.warning(f"Could not load invoice {invoice_id} from blob: {e}")

    if not xml_data:
        raise HTTPException(status_code=404, detail="Invoice not found or XML not available")

    rules_engine = get_rules_engine()

    # Get custom rules and uploaded documents from cache (if any)
    custom_rules = custom_rules_cache.get(invoice_id, [])
    uploaded_docs = uploaded_documents_cache.get(invoice_id, [])

    # Build flat data for validation
    flat_data = xml_to_dict(xml_data) if xml_data else {}

    # Add uploaded document data
    for doc in uploaded_docs:
        prefix = doc.document_type.value
        for field_name, field in doc.extracted_fields.items():
            flat_data[f"{prefix}_{field_name}"] = field.value

    # Run validation
    result = rules_engine.validate(
        invoice_id=invoice_id,
        xml_data=xml_data,
        documents=uploaded_docs,
        custom_rules=custom_rules,
        flat_data=flat_data,
    )

    return ApiResponse(
        success=True,
        message=f"Validation complete: {result.passed} passed, {result.blocking_failures} blocking, {result.warnings} warnings",
        data={
            "invoice_id": result.invoice_id,
            "timestamp": result.timestamp.isoformat(),
            "results": [r.model_dump() for r in result.results],
            "blocking_failures": result.blocking_failures,
            "warnings": result.warnings,
            "passed": result.passed,
            "can_submit": result.can_submit,
        },
    )


# Get validation rules
@app.get("/api/rules", response_model=ApiResponse)
async def get_rules(invoice_id: Optional[str] = Query(None)):
    """Get all validation rules"""
    rules_engine = get_rules_engine()
    custom_rules = custom_rules_cache.get(invoice_id, []) if invoice_id else []

    rules = rules_engine.get_all_rules(custom_rules)

    return ApiResponse(
        success=True,
        message=f"Retrieved {len(rules)} rules",
        data=rules,
    )


# Add custom rule
@app.post("/api/rules/custom", response_model=ApiResponse)
async def add_custom_rule(
    request: CustomRuleRequest,
    invoice_id: str = Query(...),
):
    """Add a custom validation rule"""
    if invoice_id not in custom_rules_cache:
        custom_rules_cache[invoice_id] = []

    custom_rules_cache[invoice_id].append(request.rule)

    return ApiResponse(
        success=True,
        message=f"Custom rule '{request.rule.nombre}' added",
        data=request.rule.model_dump(),
    )


# Delete custom rule
@app.delete("/api/rules/custom/{rule_id}", response_model=ApiResponse)
async def delete_custom_rule(
    rule_id: str,
    invoice_id: str = Query(...),
):
    """Delete a custom rule"""
    if invoice_id not in custom_rules_cache:
        raise HTTPException(status_code=404, detail="No custom rules for this invoice")

    rules = custom_rules_cache[invoice_id]
    original_len = len(rules)
    custom_rules_cache[invoice_id] = [r for r in rules if r.id != rule_id]

    if len(custom_rules_cache[invoice_id]) == original_len:
        raise HTTPException(status_code=404, detail="Rule not found")

    return ApiResponse(
        success=True,
        message=f"Rule {rule_id} deleted",
    )


# Chat endpoint
@app.post("/api/chat", response_model=ApiResponse)
async def chat(request: ChatRequest):
    """Chat with Azure OpenAI for rule definition"""
    try:
        logger.info(f"Chat request received: invoice_id={request.invoice_id}, message={request.message[:50] if len(request.message) > 50 else request.message}")

        openai_service = get_openai_service()

        # Get invoice data for context (excluding confidential data like NIT and company names)
        invoice_data = {}
        if request.invoice_id and request.invoice_id in invoice_cache:
            package = invoice_cache[request.invoice_id]
            if package.xml_data:
                full_data = xml_to_dict(package.xml_data)
                # Exclude confidential fields (NIT and company names)
                confidential_fields = [
                    'supplier_nit', 'supplier_name', 'supplier_registration_name',
                    'customer_nit', 'customer_name', 'customer_registration_name',
                    'supplier_company_id', 'customer_company_id'
                ]
                invoice_data = {k: v for k, v in full_data.items() if k.lower() not in [f.lower() for f in confidential_fields]}

            # Add uploaded document context (excluding confidential fields)
            uploaded_docs = uploaded_documents_cache.get(request.invoice_id, [])
            for doc in uploaded_docs:
                for field_name, field in doc.extracted_fields.items():
                    # Skip confidential fields from documents too
                    if any(conf in field_name.lower() for conf in ['nit', 'nombre', 'razon_social', 'company']):
                        continue
                    invoice_data[f"{doc.document_type.value}_{field_name}"] = field.value

        # Add validation results if provided
        if request.validation_results:
            invoice_data['_validation_results'] = request.validation_results

        # Add OC discrepancies if provided
        if request.oc_discrepancies:
            invoice_data['_oc_discrepancies'] = request.oc_discrepancies

        # Add invoice totals if provided (already sanitized by frontend)
        if request.invoice_totals:
            for key, value in request.invoice_totals.items():
                invoice_data[key] = value

        # Get existing rules
        existing_rules = custom_rules_cache.get(request.invoice_id, [])
        logger.info(f"Chat context: invoice_data_keys={list(invoice_data.keys())[:5]}, rules_count={len(existing_rules)}")

        # Call OpenAI
        response = openai_service.chat(
            request=request,
            invoice_data=invoice_data,
            existing_rules=existing_rules,
        )

        return ApiResponse(
            success=True,
            message="Chat response generated",
            data={
                "response": response.response,
                "rule": response.rule.model_dump() if response.rule else None,
                "conversation_history": [m.model_dump() for m in response.conversation_history],
            },
        )
    except Exception as e:
        logger.error(f"Error in chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# Submit to SAP (simulated)
@app.post("/api/invoices/{invoice_id}/submit", response_model=ApiResponse)
async def submit_invoice(invoice_id: str, request: SubmitRequest):
    """Simulate submission to SAP"""
    package = None
    xml_data = None
    validation_result = None

    # Try to get from cache first
    if invoice_id in invoice_cache:
        package = invoice_cache[invoice_id]
        xml_data = package.xml_data
        validation_result = package.validation_result
    else:
        # Not in cache - try to load from blob storage on-the-fly
        try:
            blob_service = get_blob_service()

            # Find the invoice in blob storage
            invoices = blob_service.list_invoices()
            inv = next((i for i in invoices if i["invoice_id"] == invoice_id), None)

            if inv and inv.get("path"):
                # Extract ZIP and parse XML
                extracted_files = blob_service.extract_zip(inv["path"])
                categories = blob_service.categorize_extracted_files(extracted_files)

                xml_files = categories.get('xml', [])
                if xml_files:
                    xml_content = extracted_files[xml_files[0]].decode('utf-8')
                    xml_data = parse_dian_xml(xml_content, xml_files[0])
        except Exception as e:
            logger.warning(f"Could not load invoice {invoice_id} from blob: {e}")

    if not xml_data:
        raise HTTPException(status_code=404, detail="Invoice not found or XML not available")

    # If no validation result in cache, run validation now
    if not validation_result:
        rules_engine = get_rules_engine()
        custom_rules = custom_rules_cache.get(invoice_id, [])
        uploaded_docs = uploaded_documents_cache.get(invoice_id, [])

        # Build flat data for validation
        flat_data = xml_to_dict(xml_data) if xml_data else {}

        # Add uploaded document data
        for doc in uploaded_docs:
            prefix = doc.document_type.value
            for field_name, field in doc.extracted_fields.items():
                flat_data[f"{prefix}_{field_name}"] = field.value

        # Run validation
        validation_result = rules_engine.validate(
            invoice_id=invoice_id,
            xml_data=xml_data,
            flat_data=flat_data,
            custom_rules=custom_rules,
        )

    # Check if can submit (unless force=True)
    if not request.force and not validation_result.can_submit:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot submit: {validation_result.blocking_failures} blocking failures. Use force=true to submit anyway.",
        )

    # Simulate SAP submission
    sap_doc_number = f"SAP-{invoice_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    return ApiResponse(
        success=True,
        message="Invoice submitted to SAP" + (" (forced despite failures)" if request.force and not validation_result.can_submit else ""),
        data={
            "invoice_id": invoice_id,
            "invoice_number": xml_data.invoice_number if xml_data else None,
            "sap_document_number": sap_doc_number,
            "submitted_at": datetime.now().isoformat(),
            "selected_fields": request.selected_fields,
            "notes": request.notes,
            "total_amount": xml_data.monetary_total.payable_amount if xml_data else None,
            "forced": request.force and not validation_result.can_submit,
            "validation_passed": validation_result.can_submit,
        },
    )


# Notify endpoint (for Logic App)
@app.post("/api/invoices/notify", response_model=ApiResponse)
async def notify_new_invoice(request: NotifyRequest):
    """Receive notification from Logic App about new invoice"""
    logger.info(f"Received notification for blob: {request.blobPath}")

    # Extract invoice ID
    filename = os.path.basename(request.blobPath)
    invoice_id = filename.replace('.zip', '')

    return ApiResponse(
        success=True,
        message=f"Notification received for {invoice_id}",
        data={
            "invoice_id": invoice_id,
            "blob_path": request.blobPath,
            "email_from": request.emailFrom,
            "received_at": request.receivedAt.isoformat(),
        },
    )


# Get OCR models info
@app.get("/api/ocr/models", response_model=ApiResponse)
async def get_ocr_models():
    """Get information about available OCR models"""
    doc_processor = get_document_processor()
    return ApiResponse(
        success=True,
        message="OCR models info",
        data=doc_processor.get_available_models(),
    )


# Test OCR for Formato de Cumplimiento (independent, no invoice required)
@app.post("/api/ocr/test-cumplimiento", response_model=ApiResponse)
async def test_ocr_cumplimiento(file: UploadFile = File(...)):
    """
    Test OCR processing for Formato de Cumplimiento documents.
    This endpoint is independent and does not require a processed invoice.
    The result is not stored - only for testing purposes.
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        # Read file content
        content = await file.read()

        # Get document processor
        doc_processor = get_document_processor()

        # Process with Formato Cumplimiento model
        processed_doc = doc_processor.process_pdf(
            content=content,
            filename=file.filename,
            doc_type=OCRDocumentType.FORMATO_CUMPLIMIENTO
        )

        # Return processed document info (not stored)
        return ApiResponse(
            success=True,
            message=f"Document processed successfully with {len(processed_doc.extracted_fields)} fields extracted",
            data=doc_processor.get_document_summary(processed_doc),
        )

    except Exception as e:
        logger.error(f"Error testing OCR cumplimiento: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============ EXCEL DEMO (Pandas) ============

@app.post("/api/invoices/{invoice_id}/upload-excel", response_model=ApiResponse)
async def upload_excel_for_demo(
    invoice_id: str,
    file: UploadFile = File(...)
):
    """
    Upload an Excel file for Pandas demo
    Shows how to read and display Excel data using Pandas
    """
    if invoice_id not in invoice_cache:
        raise HTTPException(status_code=404, detail="Invoice not processed. Process the invoice first.")

    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Only Excel files (.xlsx, .xls) are supported")

    try:
        # Read file content
        content = await file.read()

        # Get Excel processor
        excel_processor = get_excel_processor()

        # Process Excel (auto-detect type)
        doc_type, data = excel_processor.process_excel(content, file.filename)

        # Get summary for display
        summary = excel_processor.get_excel_summary(doc_type, data)
        summary["file_name"] = file.filename

        # Store in cache
        if invoice_id not in excel_data_cache:
            excel_data_cache[invoice_id] = {}

        excel_data_cache[invoice_id][summary["type"]] = summary

        return ApiResponse(
            success=True,
            message=f"Excel processed: {summary['type_display']} - {summary['row_count']} filas",
            data=summary,
        )

    except Exception as e:
        logger.error(f"Error processing Excel file: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/invoices/{invoice_id}/excel", response_model=ApiResponse)
async def get_excel_data(invoice_id: str):
    """Get uploaded Excel data for an invoice"""
    if invoice_id not in invoice_cache:
        raise HTTPException(status_code=404, detail="Invoice not processed")

    excel_data = excel_data_cache.get(invoice_id, {})

    return ApiResponse(
        success=True,
        message=f"Retrieved {len(excel_data)} Excel files",
        data=excel_data,
    )


@app.delete("/api/invoices/{invoice_id}/excel/{excel_type}", response_model=ApiResponse)
async def delete_excel_data(invoice_id: str, excel_type: str):
    """Delete uploaded Excel data"""
    if invoice_id not in excel_data_cache:
        raise HTTPException(status_code=404, detail="No Excel data found")

    if excel_type not in excel_data_cache[invoice_id]:
        raise HTTPException(status_code=404, detail=f"Excel type '{excel_type}' not found")

    del excel_data_cache[invoice_id][excel_type]

    return ApiResponse(
        success=True,
        message=f"Excel data '{excel_type}' deleted",
    )


# Upload XML directly (alternative to email flow)
@app.post("/api/invoices/upload-xml", response_model=ApiResponse)
async def upload_xml_directly(file: UploadFile = File(...)):
    """
    Upload an XML invoice file directly (alternative to email/blob flow)
    This allows testing without the Logic App/Blob Storage integration
    """
    if not file.filename.lower().endswith('.xml'):
        raise HTTPException(status_code=400, detail="Only XML files are supported")

    try:
        # Read file content
        content = await file.read()
        xml_content = content.decode('utf-8')

        # Generate invoice ID from filename
        invoice_id = file.filename.replace('.xml', '').replace(' ', '_')

        # Parse XML
        xml_data = parse_dian_xml(xml_content, file.filename)

        # Create package
        package = InvoicePackage(
            invoice_id=invoice_id,
            blob_path=f"uploads/{file.filename}",
            received_at=datetime.now(),
            status="processed",
            processing_steps=[
                ProcessingStep(
                    step_number=1,
                    name="Carga Manual XML",
                    status="completed",
                    started_at=datetime.now(),
                    completed_at=datetime.now(),
                    message=f"XML cargado: {file.filename}",
                ),
                ProcessingStep(
                    step_number=2,
                    name="Parseo XML DIAN",
                    status="completed",
                    started_at=datetime.now(),
                    completed_at=datetime.now(),
                    message=f"Factura {xml_data.invoice_number} parseada correctamente",
                ),
                ProcessingStep(
                    step_number=3,
                    name="Listo para Validación",
                    status="completed",
                    started_at=datetime.now(),
                    completed_at=datetime.now(),
                    message="Datos extraídos, puede proceder con validación",
                ),
            ],
            xml_data=xml_data,
        )

        # Cache the package
        invoice_cache[invoice_id] = package

        # Initialize empty uploaded documents
        if invoice_id not in uploaded_documents_cache:
            uploaded_documents_cache[invoice_id] = []

        return ApiResponse(
            success=True,
            message=f"XML processed successfully: {xml_data.invoice_number}",
            data=_serialize_package(package),
        )

    except Exception as e:
        logger.error(f"Error processing uploaded XML: {e}")
        raise HTTPException(status_code=500, detail=f"Error parsing XML: {str(e)}")


# Helper function to serialize package
def _serialize_package(package: InvoicePackage) -> Dict[str, Any]:
    """Serialize InvoicePackage for API response"""
    uploaded_docs = uploaded_documents_cache.get(package.invoice_id, [])
    has_oc = any(d.document_type == DocumentType.ORDEN_COMPRA for d in uploaded_docs)

    # Build attachment references with found status
    attachment_refs = []
    if package.xml_data:
        for ref in package.xml_data.attachment_references:
            ref_dict = {
                "reference_id": ref.reference_id,
                "reference_type": ref.reference_type,
                "description": ref.description,
                "found_in_zip": ref.reference_type == "orden_compra" and has_oc,
            }
            attachment_refs.append(ref_dict)

    return {
        "invoice_id": package.invoice_id,
        "blob_path": package.blob_path,
        "received_at": package.received_at.isoformat(),
        "status": package.status,
        "xml_data": {
            "invoice_number": package.xml_data.invoice_number,
            "supplier_name": package.xml_data.supplier.registration_name,
            "supplier_nit": package.xml_data.supplier.company_id,
            "supplier_tax_level": package.xml_data.supplier.tax_level_code,
            "customer_name": package.xml_data.customer.registration_name,
            "customer_nit": package.xml_data.customer.company_id,
            "customer_tax_level": package.xml_data.customer.tax_level_code,
            "subtotal": package.xml_data.monetary_total.line_extension_amount,
            "total_iva": package.xml_data.total_iva,
            "total_retenciones": package.xml_data.total_retenciones,
            "total_pagable": package.xml_data.monetary_total.payable_amount,
            "issue_date": package.xml_data.issue_date.isoformat() if package.xml_data.issue_date else None,
            "due_date": package.xml_data.due_date.isoformat() if package.xml_data.due_date else None,
            "orden_compra": package.xml_data.order_reference.order_id if package.xml_data.order_reference else None,
            "line_count": package.xml_data.line_count,
            "items": [
                {
                    "line_id": line.line_id,
                    "description": line.description[:100] + "..." if len(line.description) > 100 else line.description,
                    "quantity": line.quantity,
                    "unit_code": line.unit_code,
                    "unit_price": line.unit_price,
                    "line_total": line.line_extension_amount,
                }
                for line in package.xml_data.lines
            ],
            "attachment_references": attachment_refs,
        } if package.xml_data else None,
        "documents_count": len(uploaded_docs),
        "has_orden_compra": has_oc,
        "has_cumplimiento": any(d.document_type == DocumentType.FORMATO_CUMPLIMIENTO for d in uploaded_docs),
        "processing_steps": [
            {
                "step_number": s.step_number,
                "name": s.name,
                "status": s.status,
                "message": s.message,
            }
            for s in package.processing_steps
        ],
        "validation_result": {
            "can_submit": package.validation_result.can_submit,
            "blocking_failures": package.validation_result.blocking_failures,
            "warnings": package.validation_result.warnings,
            "passed": package.validation_result.passed,
        } if package.validation_result else None,
    }


# Run with uvicorn for local development
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7071)
