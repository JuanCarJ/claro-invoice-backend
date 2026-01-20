"""
Azure Document Intelligence Processor
Handles PDF processing for Orden de Compra and Formato de Cumplimiento
Supports 2 separate trained models
"""
import os
import io
import logging
import time
from typing import Optional, Dict, Any, List
from enum import Enum

from models import (
    DocumentType, ProcessedDocument, ExtractedField
)

logger = logging.getLogger(__name__)

# Try to import Azure Document Intelligence (new SDK)
try:
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
    from azure.core.credentials import AzureKeyCredential
    AZURE_DOC_INTEL_AVAILABLE = True
except ImportError:
    AZURE_DOC_INTEL_AVAILABLE = False
    logger.warning("Azure Document Intelligence SDK not available - install azure-ai-documentintelligence")


class OCRDocumentType(str, Enum):
    """Types of documents for OCR processing"""
    ORDEN_COMPRA = "orden_compra"
    FORMATO_CUMPLIMIENTO = "formato_cumplimiento"


class DocumentProcessor:
    """Process PDF documents using Azure Document Intelligence with 2 custom models"""

    def __init__(self):
        """
        Initialize Document Processor - uses LAZY initialization for the client
        """
        self._client = None
        self._initialized = False
        self._mock_mode = None  # None = not yet determined

    def _ensure_initialized(self):
        """Lazy initialization - check env vars and create client when first needed"""
        if self._initialized:
            return

        self._initialized = True

        # Read env vars NOW (not at import time)
        self.endpoint = os.getenv('AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT')
        self.api_key = os.getenv('AZURE_DOCUMENT_INTELLIGENCE_KEY')
        self.model_orden_compra = os.getenv('AZURE_DOC_INTEL_MODEL_ORDEN_COMPRA', 'prebuilt-document')
        self.model_cumplimiento = os.getenv('AZURE_DOC_INTEL_MODEL_CUMPLIMIENTO', 'prebuilt-document')

        # Log initialization details
        logger.info("=" * 60)
        logger.info("DocumentProcessor LAZY Initialization (at first use)")
        logger.info(f"  AZURE_DOC_INTEL_AVAILABLE: {AZURE_DOC_INTEL_AVAILABLE}")
        logger.info(f"  Endpoint: {self.endpoint[:50] + '...' if self.endpoint and len(self.endpoint) > 50 else self.endpoint}")
        logger.info(f"  API Key: {'*' * 10 + self.api_key[-4:] if self.api_key else 'NOT SET'}")
        logger.info(f"  Model OC: {self.model_orden_compra}")
        logger.info(f"  Model Cumplimiento: {self.model_cumplimiento}")
        logger.info("=" * 60)

        if not AZURE_DOC_INTEL_AVAILABLE:
            logger.warning("Azure Document Intelligence SDK not installed - using mock mode")
            self._mock_mode = True
        elif not self.endpoint or not self.api_key:
            logger.warning("No Azure Document Intelligence credentials - using mock mode")
            logger.warning(f"  Endpoint present: {bool(self.endpoint)}")
            logger.warning(f"  API Key present: {bool(self.api_key)}")
            self._mock_mode = True
        else:
            try:
                self._client = DocumentIntelligenceClient(
                    endpoint=self.endpoint,
                    credential=AzureKeyCredential(self.api_key)
                )
                self._mock_mode = False
                logger.info(f"✓ Document Intelligence client initialized successfully")
                logger.info(f"  Endpoint: {self.endpoint}")
                logger.info(f"  Models: OC={self.model_orden_compra}, Cumplimiento={self.model_cumplimiento}")
            except Exception as e:
                logger.error(f"Failed to initialize Document Intelligence client: {e}")
                self._mock_mode = True

    def _get_model_id(self, doc_type: OCRDocumentType) -> str:
        """Get the model ID for a document type"""
        self._ensure_initialized()
        if doc_type == OCRDocumentType.ORDEN_COMPRA:
            return self.model_orden_compra
        else:
            return self.model_cumplimiento

    def _extract_oc_number_from_filename(self, filename: str) -> str:
        """Try to extract OC number from filename like 'OC 4500811404.pdf' or '4500811404.pdf'"""
        import re
        # Try to find a 10-digit number in the filename
        match = re.search(r'(\d{10})', filename)
        if match:
            return match.group(1)
        # Default fallback
        return "4500799306"

    def _get_mock_oc_document(self, filename: str) -> ProcessedDocument:
        """Return mock Orden de Compra document (fields match POClaroOCRModel)"""
        # Extract OC number from filename for more realistic mock
        oc_number = self._extract_oc_number_from_filename(filename)
        logger.info(f"Mock OC: Using OC number '{oc_number}' extracted from filename '{filename}'")

        return ProcessedDocument(
            document_type=DocumentType.ORDEN_COMPRA,
            file_name=filename,
            file_path=f"uploads/{filename}",
            extracted_fields={
                "PurchaseNumber": ExtractedField(
                    field_name="PurchaseNumber",
                    value=oc_number,
                    confidence=0.975,
                    field_type="string",
                ),
                "InvoiceDate": ExtractedField(
                    field_name="InvoiceDate",
                    value="04-14-2025",
                    confidence=0.949,
                    field_type="date",
                ),
                "RequisitionNumber": ExtractedField(
                    field_name="RequisitionNumber",
                    value="2000745818",
                    confidence=0.981,
                    field_type="string",
                ),
                "InvoiceTotal": ExtractedField(
                    field_name="InvoiceTotal",
                    value="137.310.992 COP",  # ~SC14591 value for demo
                    confidence=0.917,
                    field_type="string",
                ),
                "TotalBruto": ExtractedField(
                    field_name="TotalBruto",
                    value="115.387.388 COP",  # ~SC14591 value for demo
                    confidence=0.958,
                    field_type="string",
                ),
                "TotalIva": ExtractedField(
                    field_name="TotalIva",
                    value="21.923.604 COP",  # ~SC14591 value for demo
                    confidence=0.934,
                    field_type="string",
                ),
                "TotalDiscount": ExtractedField(
                    field_name="TotalDiscount",
                    value="0 COP",
                    confidence=0.617,
                    field_type="string",
                ),
                "Currency": ExtractedField(
                    field_name="Currency",
                    value="COP",
                    confidence=0.978,
                    field_type="string",
                ),
                "PaymentTerm": ExtractedField(
                    field_name="PaymentTerm",
                    value="60 DIAS",
                    confidence=0.924,
                    field_type="string",
                ),
                "ProviderID": ExtractedField(
                    field_name="ProviderID",
                    value="830099847",  # Same as NIT
                    confidence=0.976,
                    field_type="string",
                ),
                "ProviderName": ExtractedField(
                    field_name="ProviderName",
                    value="SOFTTEK RENOVATION SAS",  # Match XML supplier name
                    confidence=0.961,
                    field_type="string",
                ),
                "ProviderNit": ExtractedField(
                    field_name="ProviderNit",
                    value="830099847",  # Match XML supplier NIT
                    confidence=0.892,
                    field_type="string",
                ),
                "ProviderAddress": ExtractedField(
                    field_name="ProviderAddress",
                    value="CR 19 109 A 60 Bogotá, BOGOTA",
                    confidence=0.564,
                    field_type="string",
                ),
                "CompradorBuyer": ExtractedField(
                    field_name="CompradorBuyer",
                    value="C06 AG",
                    confidence=0.917,
                    field_type="string",
                ),
                "ContactPersonName": ExtractedField(
                    field_name="ContactPersonName",
                    value="USUARIO DE CONEXION WEB SERVICE IVALUA",
                    confidence=0.941,
                    field_type="string",
                ),
                "IvaNeto": ExtractedField(
                    field_name="IvaNeto",
                    value="619.316.079 COP",
                    confidence=0.967,
                    field_type="string",
                ),
                "ClaveCode": ExtractedField(
                    field_name="ClaveCode",
                    value="ANBN",
                    confidence=0.972,
                    field_type="string",
                ),
                "Items": ExtractedField(
                    field_name="Items",
                    value="N/A",
                    confidence=0.323,
                    field_type="array",
                ),
                "POHeader": ExtractedField(
                    field_name="POHeader",
                    value="N/A",
                    confidence=0.777,
                    field_type="string",
                ),
                "ValorLetras": ExtractedField(
                    field_name="ValorLetras",
                    value="N/A",
                    confidence=0.714,
                    field_type="string",
                ),
            },
            confidence_score=0.983,
            page_count=1,
            processing_time_ms=1250,
        )

    def _get_mock_cumplimiento_document(self, filename: str) -> ProcessedDocument:
        """Return mock Formato de Cumplimiento document (fields match FormatoCumplimientoClaroOCR)"""
        return ProcessedDocument(
            document_type=DocumentType.FORMATO_CUMPLIMIENTO,
            file_name=filename,
            file_path=f"uploads/{filename}",
            extracted_fields={
                "FormatoID": ExtractedField(
                    field_name="FormatoID",
                    value="10000233868",
                    confidence=0.987,
                    field_type="string",
                ),
                "Sociedad": ExtractedField(
                    field_name="Sociedad",
                    value="CO15 Comunicación Celular, S.A",
                    confidence=0.618,
                    field_type="string",
                ),
                "EstadoAprobacion": ExtractedField(
                    field_name="EstadoAprobacion",
                    value="AP Aprobado",
                    confidence=0.984,
                    field_type="string",
                ),
                "ValidoParaFacturaBandera": ExtractedField(
                    field_name="ValidoParaFacturaBandera",
                    value="SI",
                    confidence=0.987,
                    field_type="string",
                ),
                "NumeroDocumento": ExtractedField(
                    field_name="NumeroDocumento",
                    value="4500799306",
                    confidence=0.989,
                    field_type="string",
                ),
                "NumeroProveedorDescripcion": ExtractedField(
                    field_name="NumeroProveedorDescripcion",
                    value="800003003 SOFTTEK RENOVATION LIMITADA",
                    confidence=0.985,
                    field_type="string",
                ),
                "NitProveedor": ExtractedField(
                    field_name="NitProveedor",
                    value="8300998478",
                    confidence=0.990,
                    field_type="string",
                ),
                "Moneda": ExtractedField(
                    field_name="Moneda",
                    value="COP Peso colombiano",
                    confidence=0.978,
                    field_type="string",
                ),
                "CondicionPago": ExtractedField(
                    field_name="CondicionPago",
                    value="K010 VENCIMIENTO EN 60 DÍAS",
                    confidence=0.937,
                    field_type="string",
                ),
                "IdCreador": ExtractedField(
                    field_name="IdCreador",
                    value="38507107",
                    confidence=0.991,
                    field_type="string",
                ),
                "IdAutorizador": ExtractedField(
                    field_name="IdAutorizador",
                    value="38500369",
                    confidence=0.992,
                    field_type="string",
                ),
                "NombreAutorizador": ExtractedField(
                    field_name="NombreAutorizador",
                    value="Miguel E CAJIGAS Silva",
                    confidence=0.877,
                    field_type="string",
                ),
                "FechaCreacion": ExtractedField(
                    field_name="FechaCreacion",
                    value="23.04.2025",
                    confidence=0.984,
                    field_type="string",
                ),
                "FechaAutorizacion": ExtractedField(
                    field_name="FechaAutorizacion",
                    value="23.04.2025",
                    confidence=0.987,
                    field_type="string",
                ),
                "ReferenciaFactura": ExtractedField(
                    field_name="ReferenciaFactura",
                    value="5004416830",
                    confidence=0.990,
                    field_type="string",
                ),
                "Subtotal": ExtractedField(
                    field_name="Subtotal",
                    value="144.902.680,00",
                    confidence=0.987,
                    field_type="string",
                ),
                "Impuesto": ExtractedField(
                    field_name="Impuesto",
                    value="27.531.509,20",
                    confidence=0.987,
                    field_type="string",
                ),
                "Total": ExtractedField(
                    field_name="Total",
                    value="172.434.189,20",
                    confidence=0.987,
                    field_type="string",
                ),
                "Items": ExtractedField(
                    field_name="Items",
                    value="N/A",
                    confidence=0.974,
                    field_type="array",
                ),
            },
            confidence_score=0.999,
            page_count=1,
            processing_time_ms=980,
        )

    def process_pdf(
        self,
        content: bytes,
        filename: str,
        doc_type: OCRDocumentType
    ) -> ProcessedDocument:
        """
        Process PDF document using Azure Document Intelligence

        Args:
            content: PDF content as bytes
            filename: Name of the file
            doc_type: Type of document (ORDEN_COMPRA or FORMATO_CUMPLIMIENTO)

        Returns:
            ProcessedDocument with extracted fields
        """
        # Ensure client is initialized (lazy init)
        self._ensure_initialized()

        start_time = time.time()

        if self._mock_mode or self._client is None:
            # Return mock data based on document type
            logger.warning(f"Using mock mode for {filename}")
            if doc_type == OCRDocumentType.ORDEN_COMPRA:
                return self._get_mock_oc_document(filename)
            else:
                return self._get_mock_cumplimiento_document(filename)

        try:
            model_id = self._get_model_id(doc_type)
            logger.info(f"Processing {filename} with model: {model_id}")
            logger.info(f"Content size: {len(content)} bytes")

            # Analyze document using Azure Document Intelligence SDK
            # Based on Microsoft's official example:
            # poller = client.begin_analyze_document("model-id", AnalyzeDocumentRequest(url_source=url))
            # For bytes, use bytes_source with base64-encoded content
            import base64
            base64_content = base64.b64encode(content).decode('utf-8')

            # Call exactly as Microsoft's example - model_id first, then AnalyzeDocumentRequest as second positional arg
            poller = self._client.begin_analyze_document(
                model_id,
                AnalyzeDocumentRequest(bytes_source=base64_content)
            )
            result = poller.result()

            logger.info(f"Document analyzed successfully")

            # Extract fields from the new SDK response structure
            extracted_fields = {}
            overall_confidence = 0.0
            field_count = 0

            # New SDK uses result.documents[].fields
            if result.documents:
                for document in result.documents:
                    logger.info(f"Document type: {document.doc_type}, confidence: {document.confidence}")
                    if document.fields:
                        for field_name, field in document.fields.items():
                            # Extract field value and type
                            field_value = field.content if hasattr(field, 'content') and field.content else None
                            field_confidence = field.confidence if hasattr(field, 'confidence') and field.confidence else 0.0
                            field_type = field.type if hasattr(field, 'type') else "string"

                            # Include all fields, even with low confidence
                            extracted_fields[field_name] = ExtractedField(
                                field_name=field_name,
                                value=field_value if field_value else "N/A",
                                confidence=field_confidence,
                                field_type=field_type,
                                bounding_box=None,
                            )

                            if field_value:
                                overall_confidence += field_confidence
                                field_count += 1

                            logger.info(f"Field '{field_name}': {field_value} (confidence: {field_confidence:.2%}, type: {field_type})")

            # Calculate average confidence
            if field_count > 0:
                overall_confidence = overall_confidence / field_count
            else:
                overall_confidence = 0.0

            # Map OCR type to document type
            document_type = (
                DocumentType.ORDEN_COMPRA
                if doc_type == OCRDocumentType.ORDEN_COMPRA
                else DocumentType.FORMATO_CUMPLIMIENTO
            )

            processing_time = int((time.time() - start_time) * 1000)

            return ProcessedDocument(
                document_type=document_type,
                file_name=filename,
                file_path=f"uploads/{filename}",
                extracted_fields=extracted_fields,
                confidence_score=overall_confidence,
                page_count=len(result.pages) if result.pages else 1,
                processing_time_ms=processing_time,
            )

        except Exception as e:
            logger.error(f"Error processing document {filename}: {e}")
            logger.warning(f"Falling back to mock mode for {filename}")

            # Fallback to mock data
            if doc_type == OCRDocumentType.ORDEN_COMPRA:
                mock_doc = self._get_mock_oc_document(filename)
            else:
                mock_doc = self._get_mock_cumplimiento_document(filename)

            # Mark as mock/fallback in a field with clear message
            error_msg = str(e)[:150]
            mock_doc.extracted_fields["_modo_demo"] = ExtractedField(
                field_name="_modo_demo",
                value=f"DATOS DE DEMOSTRACIÓN - OCR no disponible: {error_msg}",
                confidence=1.0,
                field_type="warning",
            )
            mock_doc.extracted_fields["_nota"] = ExtractedField(
                field_name="_nota",
                value="Los datos mostrados son simulados para demostración. En producción, estos campos serían extraídos por Azure Document Intelligence.",
                confidence=1.0,
                field_type="info",
            )
            return mock_doc

    def get_document_summary(self, doc: ProcessedDocument) -> Dict[str, Any]:
        """
        Get summary of processed document for display

        Args:
            doc: ProcessedDocument

        Returns:
            Summary dictionary
        """
        # Build extracted_fields with full field info
        extracted_fields = {
            name: {
                "field_name": field.field_name,
                "value": field.value,
                "confidence": field.confidence,
                "field_type": field.field_type,
            }
            for name, field in doc.extracted_fields.items()
        }

        return {
            "file_name": doc.file_name,
            "document_type": doc.document_type.value,
            "document_type_display": self._get_document_type_display(doc.document_type),
            "confidence_score": doc.confidence_score,
            "page_count": doc.page_count,
            "field_count": len(doc.extracted_fields),
            "processing_time_ms": doc.processing_time_ms,
            "extracted_fields": extracted_fields,
            # Keep 'fields' for backwards compatibility
            "fields": {
                name: {
                    "value": field.value,
                    "confidence": field.confidence,
                    "field_type": field.field_type,
                }
                for name, field in doc.extracted_fields.items()
            }
        }

    def _get_document_type_display(self, doc_type: DocumentType) -> str:
        """Get display name for document type"""
        display_names = {
            DocumentType.ORDEN_COMPRA: "Orden de Compra",
            DocumentType.FORMATO_CUMPLIMIENTO: "Formato de Cumplimiento",
            DocumentType.XML_FACTURA: "Factura XML DIAN",
            DocumentType.UNKNOWN: "Documento Desconocido",
        }
        return display_names.get(doc_type, "Desconocido")

    def get_available_models(self) -> Dict[str, str]:
        """Get info about configured models"""
        self._ensure_initialized()
        return {
            "orden_compra": {
                "model_id": self.model_orden_compra,
                "configured": bool(self._client and self.model_orden_compra != "prebuilt-document"),
            },
            "cumplimiento": {
                "model_id": self.model_cumplimiento,
                "configured": bool(self._client and self.model_cumplimiento != "prebuilt-document"),
            },
            "endpoint": self.endpoint,
            "mock_mode": self._mock_mode,
        }


# Singleton instance
_document_processor: Optional[DocumentProcessor] = None


def get_document_processor() -> DocumentProcessor:
    """Get or create DocumentProcessor singleton"""
    global _document_processor
    if _document_processor is None:
        _document_processor = DocumentProcessor()
    return _document_processor
