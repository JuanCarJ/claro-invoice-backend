"""
Pydantic models for the Claro Invoice Automation Demo
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import date, datetime
from enum import Enum


class DocumentType(str, Enum):
    ORDEN_COMPRA = "orden_compra"
    FORMATO_CUMPLIMIENTO = "formato_cumplimiento"
    EXCEL_MANO_OBRA = "excel_mano_obra"
    EXCEL_FABRICANTES = "excel_fabricantes"
    XML_FACTURA = "xml_factura"
    UNKNOWN = "unknown"


class RuleType(str, Enum):
    BLOCKING = "blocking"
    WARNING = "warning"


class RuleStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


# XML DIAN Models
class TaxDetail(BaseModel):
    """Detalle de impuesto (IVA, ReteICA, ReteRenta, ReteIVA)"""
    tax_scheme_id: str = Field(..., description="ID del esquema de impuesto (01=IVA, 05=ReteIVA, 06=ReteRenta, 07=ReteICA)")
    tax_name: str = Field(..., description="Nombre del impuesto")
    taxable_amount: float = Field(..., description="Base gravable")
    tax_percentage: float = Field(..., description="Porcentaje del impuesto")
    tax_amount: float = Field(..., description="Valor del impuesto")


class PartyInfo(BaseModel):
    """Información de una parte (emisor/adquirente)"""
    company_id: str = Field(..., description="NIT")
    registration_name: str = Field(..., description="Razón social")
    tax_level_code: Optional[str] = Field(None, description="Tipo contribuyente (O-23, O-15, etc.)")
    tax_scheme_id: Optional[str] = Field(None, description="Régimen tributario")
    address_line: Optional[str] = Field(None, description="Dirección")
    city_name: Optional[str] = Field(None, description="Ciudad")
    department: Optional[str] = Field(None, description="Departamento")
    country_code: Optional[str] = Field(None, description="Código país")
    email: Optional[str] = Field(None, description="Email de contacto")
    phone: Optional[str] = Field(None, description="Teléfono")
    supplier_assigned_account_id: Optional[str] = Field(None, description="Código proveedor en el cliente")


class InvoiceLine(BaseModel):
    """Línea de producto/servicio en la factura"""
    line_id: str = Field(..., description="ID de la línea")
    description: str = Field(..., description="Descripción del producto/servicio")
    quantity: float = Field(..., description="Cantidad")
    unit_code: str = Field(..., description="Código unidad (HUR=Horas, EA=Unidad)")
    unit_price: float = Field(..., description="Precio unitario")
    line_extension_amount: float = Field(..., description="Total línea (sin IVA)")
    product_code: Optional[str] = Field(None, description="Código del producto")
    tax_amount: Optional[float] = Field(None, description="IVA de la línea")


class OrderReference(BaseModel):
    """Referencia a Orden de Compra"""
    order_id: str = Field(..., description="Número de OC")
    sales_order_id: Optional[str] = Field(None, description="Sales Order ID")


class AttachmentReference(BaseModel):
    """Referencia a un archivo adjunto mencionado en el XML"""
    reference_id: str = Field(..., description="ID de referencia (número OC, contrato, etc.)")
    reference_type: str = Field(..., description="Tipo de referencia (orden_compra, contrato, otro)")
    description: Optional[str] = Field(None, description="Descripción del adjunto")
    found_in_zip: bool = Field(default=False, description="Si se encontró el archivo en el ZIP")
    matched_filename: Optional[str] = Field(None, description="Nombre del archivo que coincide")


class PaymentMeans(BaseModel):
    """Medio de pago"""
    payment_means_id: str = Field(..., description="ID del medio de pago")
    payment_means_code: str = Field(..., description="Código medio pago (10=Efectivo, 31=Transferencia, 42=Cuenta)")
    payment_due_date: Optional[date] = Field(None, description="Fecha vencimiento pago")


class InvoiceAuthorization(BaseModel):
    """Autorización de numeración DIAN"""
    authorization_number: Optional[str] = Field(None, description="Número de resolución DIAN")
    authorization_date: Optional[date] = Field(None, description="Fecha de resolución")
    authorization_end_date: Optional[date] = Field(None, description="Fecha fin vigencia")
    prefix: Optional[str] = Field(None, description="Prefijo autorizado")
    range_from: Optional[str] = Field(None, description="Desde número")
    range_to: Optional[str] = Field(None, description="Hasta número")


class InvoicePeriod(BaseModel):
    """Período de facturación"""
    start_date: Optional[date] = Field(None, description="Fecha inicio período")
    end_date: Optional[date] = Field(None, description="Fecha fin período")
    description: Optional[str] = Field(None, description="Descripción del período")


class MonetaryTotal(BaseModel):
    """Totales monetarios de la factura"""
    line_extension_amount: float = Field(..., description="Subtotal sin IVA")
    tax_exclusive_amount: float = Field(..., description="Total sin impuestos")
    tax_inclusive_amount: float = Field(..., description="Total con IVA")
    allowance_total_amount: Optional[float] = Field(0, description="Total descuentos")
    charge_total_amount: Optional[float] = Field(0, description="Total cargos")
    payable_amount: float = Field(..., description="Total a pagar")


class XMLInvoiceData(BaseModel):
    """Datos completos extraídos del XML DIAN UBL 2.1"""
    # Identificación
    invoice_number: str = Field(..., description="Número de factura")
    cufe: str = Field(..., description="CUFE (Código Único de Factura Electrónica)")
    issue_date: date = Field(..., description="Fecha de emisión")
    issue_time: Optional[str] = Field(None, description="Hora de emisión")
    due_date: Optional[date] = Field(None, description="Fecha de vencimiento")
    currency_code: str = Field(default="COP", description="Código moneda")
    invoice_type_code: str = Field(..., description="Tipo de documento (01=Factura, 02=Exportación, 03=Contingencia, 04=Simplificada)")

    # Notas y observaciones
    notes: List[str] = Field(default_factory=list, description="Notas de la factura")

    # Partes
    supplier: PartyInfo = Field(..., description="Datos del emisor/proveedor")
    customer: PartyInfo = Field(..., description="Datos del adquirente/cliente")

    # Medios de pago
    payment_means: List[PaymentMeans] = Field(default_factory=list, description="Medios de pago")

    # Período de facturación
    invoice_period: Optional[InvoicePeriod] = Field(None, description="Período de facturación")

    # Autorización DIAN
    authorization: Optional[InvoiceAuthorization] = Field(None, description="Autorización numeración DIAN")

    # Impuestos
    taxes: List[TaxDetail] = Field(default_factory=list, description="Impuestos (IVA)")
    withholding_taxes: List[TaxDetail] = Field(default_factory=list, description="Retenciones (ReteICA, ReteRenta, ReteIVA)")

    # Totales
    monetary_total: MonetaryTotal = Field(..., description="Totales monetarios")

    # Descuentos y cargos globales
    total_discount: float = Field(default=0, description="Total descuentos")
    total_charges: float = Field(default=0, description="Total cargos adicionales")
    prepaid_amount: float = Field(default=0, description="Pagos anticipados")

    # Líneas
    lines: List[InvoiceLine] = Field(default_factory=list, description="Líneas de productos/servicios")
    line_count: int = Field(default=0, description="Cantidad de líneas")

    # Referencias
    order_reference: Optional[OrderReference] = Field(None, description="Referencia a OC")

    # QR y verificación
    qr_code: Optional[str] = Field(None, description="URL código QR DIAN")

    # Totales calculados para fácil acceso
    total_iva: float = Field(default=0, description="Total IVA (suma de impuestos tipo 01)")
    total_retenciones: float = Field(default=0, description="Total retenciones (ReteICA + ReteRenta + ReteIVA)")

    # Referencias a adjuntos
    attachment_references: List["AttachmentReference"] = Field(default_factory=list, description="Referencias a archivos adjuntos mencionados en el XML")

    # Metadata
    raw_xml_path: Optional[str] = Field(None, description="Ruta al XML original")


# Document Intelligence Models
class ExtractedField(BaseModel):
    """Campo extraído por Document Intelligence"""
    field_name: str
    value: Any
    confidence: float = Field(ge=0, le=1)
    field_type: Optional[str] = Field(None, description="Tipo de campo (string, date, number, array, etc.)")
    bounding_box: Optional[List[float]] = None


class ProcessedDocument(BaseModel):
    """Documento procesado por Document Intelligence"""
    document_type: DocumentType
    file_name: str
    file_path: str
    extracted_fields: Dict[str, ExtractedField] = Field(default_factory=dict)
    confidence_score: float = Field(ge=0, le=1)
    page_count: int = 1
    processing_time_ms: Optional[int] = None


# Excel Models
class ManoObraRow(BaseModel):
    """Fila del Excel de Mano de Obra"""
    empleado: str
    cargo: str
    horas: float
    tarifa_hora: float
    total: float


class ManoObraData(BaseModel):
    """Datos completos del Excel de Mano de Obra"""
    rows: List[ManoObraRow]
    total_horas: float
    total_valor: float


class FabricanteRow(BaseModel):
    """Fila del Excel de Fabricantes"""
    codigo: str
    descripcion: str
    fabricante: str
    cantidad: float
    valor_unitario: float
    total: float


class FabricantesData(BaseModel):
    """Datos completos del Excel de Fabricantes"""
    rows: List[FabricanteRow]
    total_valor: float


# Rules Models
class RuleCondition(BaseModel):
    """Condición de una regla"""
    campo: str
    operador: str  # >, <, ==, !=, contains, exists
    valor: Any


class ConditionalRule(BaseModel):
    """Regla condicional (if-then)"""
    if_condition: RuleCondition = Field(..., alias="if")
    then_requirement: Dict[str, Any] = Field(..., alias="then")

    class Config:
        populate_by_name = True


class ValidationRule(BaseModel):
    """Regla de validación"""
    id: str
    nombre: str
    descripcion: str
    tipo: RuleType
    fuentes: List[str] = Field(default_factory=list, description="Fuentes de datos requeridas")
    condicion: Optional[RuleCondition] = None
    condicion_condicional: Optional[ConditionalRule] = None
    is_custom: bool = False


class RuleResult(BaseModel):
    """Resultado de evaluación de una regla"""
    rule_id: str
    rule_name: str
    status: RuleStatus
    message: str
    details: Optional[Dict[str, Any]] = None


class ValidationResult(BaseModel):
    """Resultado completo de validación"""
    invoice_id: str
    timestamp: datetime
    results: List[RuleResult]
    blocking_failures: int = 0
    warnings: int = 0
    passed: int = 0
    can_submit: bool = True


# Chat Models
class ChatMessage(BaseModel):
    """Mensaje de chat"""
    role: str  # user, assistant, system
    content: str


class ChatRequest(BaseModel):
    """Request para el chatbot"""
    message: str
    invoice_id: str
    selected_fields: List[str] = Field(default_factory=list)
    conversation_history: List[ChatMessage] = Field(default_factory=list)
    # Additional context data from the flow
    validation_results: Optional[List[Dict[str, Any]]] = None  # Results of rule validation
    oc_discrepancies: Optional[List[Dict[str, Any]]] = None  # Discrepancies between XML and OC
    invoice_totals: Optional[Dict[str, Any]] = None  # Totals without confidential data
    # Full data (excluding confidential info - NIT and company names)
    invoice_data: Optional[Dict[str, Any]] = None  # Full invoice data from XML
    oc_data: Optional[Dict[str, Any]] = None  # Full OC data from OCR


class ChatResponse(BaseModel):
    """Response del chatbot"""
    response: str
    rule: Optional[ValidationRule] = None
    conversation_history: List[ChatMessage]


# Invoice Processing Models
class ProcessingStep(BaseModel):
    """Paso en el proceso de una factura"""
    step_number: int
    name: str
    status: str  # pending, in_progress, completed, error
    message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class InvoicePackage(BaseModel):
    """Paquete completo de una factura"""
    invoice_id: str
    blob_path: str
    received_at: datetime
    status: str  # pending, processing, processed, error

    # Datos procesados
    xml_data: Optional[XMLInvoiceData] = None
    documents: List[ProcessedDocument] = Field(default_factory=list)
    mano_obra_data: Optional[ManoObraData] = None
    fabricantes_data: Optional[FabricantesData] = None

    # Progreso
    processing_steps: List[ProcessingStep] = Field(default_factory=list)

    # Validación
    validation_result: Optional[ValidationResult] = None
    custom_rules: List[ValidationRule] = Field(default_factory=list)


class InvoiceListItem(BaseModel):
    """Item en la lista de facturas"""
    invoice_id: str
    invoice_number: Optional[str] = None
    supplier_name: Optional[str] = None
    total_amount: Optional[float] = None
    issue_date: Optional[date] = None
    status: str
    received_at: datetime


# API Response Models
class ApiResponse(BaseModel):
    """Response genérico de la API"""
    success: bool
    message: str
    data: Optional[Any] = None
    error: Optional[str] = None


class NotifyRequest(BaseModel):
    """Request de notificación desde Logic App"""
    blobPath: str
    emailFrom: str
    receivedAt: datetime


# ============ ATTACHMENT AND COMPARISON MODELS ============

class AttachmentInfo(BaseModel):
    """Information about a file from nested ZIP (Anexo.zip)"""
    name: str = Field(..., description="Filename")
    size: int = Field(..., description="File size in bytes")
    is_processed: bool = Field(default=False, description="Whether OCR has been run")
    document_type: Optional[str] = Field(None, description="Detected/assigned document type")
    source: str = Field(default="nested_zip", description="Source of the attachment")


class FieldComparison(BaseModel):
    """Comparison of a field between XML and OC"""
    field_name: str = Field(..., description="Field name being compared")
    field_label: str = Field(..., description="Human-readable label")
    xml_value: Optional[Any] = Field(None, description="Value from XML")
    oc_value: Optional[Any] = Field(None, description="Value from OC document")
    match: bool = Field(..., description="Whether values match")
    match_type: str = Field(..., description="Type of match: exact, partial, numeric_close, mismatch, missing_xml, missing_oc")
    notes: Optional[str] = Field(None, description="Additional notes about the comparison")


class OCComparisonResult(BaseModel):
    """Result of comparing XML data with Orden de Compra"""
    invoice_id: str = Field(..., description="Invoice ID")
    xml_oc_reference: str = Field(..., description="OC number from XML OrderReference")
    oc_document_number: Optional[str] = Field(None, description="OC number extracted from PDF")
    oc_file_name: str = Field(..., description="Name of the OC PDF file")
    comparisons: List[FieldComparison] = Field(default_factory=list, description="Field-by-field comparisons")
    overall_match: bool = Field(..., description="Whether overall comparison passes")
    match_percentage: float = Field(..., description="Percentage of matching fields")
    matched_fields: int = Field(default=0, description="Number of matched fields")
    total_fields: int = Field(default=0, description="Total fields compared")
    conclusion: str = Field(..., description="Summary conclusion in Spanish")
    conclusion_type: str = Field(default="info", description="Type: success, warning, error")


# Field mapping for XML to OC comparison
# OC field names match POClaroOCRModel: PurchaseNumber, InvoiceTotal, TotalBruto, TotalIva, ProviderNit, ProviderName
XML_TO_OC_FIELD_MAPPING = {
    "order_reference": {
        "oc_field": "PurchaseNumber",
        "label": "Número Orden de Compra",
        "compare_type": "exact"
    },
    "supplier_nit": {
        "oc_field": "ProviderNit",
        "label": "NIT Proveedor",
        "compare_type": "exact"
    },
    "supplier_name": {
        "oc_field": "ProviderName",
        "label": "Nombre Proveedor",
        "compare_type": "contains"
    },
    "total_payable": {
        "oc_field": "InvoiceTotal",  # Matches POClaroOCRModel field name
        "label": "Total a Pagar",
        "compare_type": "numeric",
        "tolerance": 0.05  # 5% tolerance
    },
    "line_extension_amount": {
        "oc_field": "TotalBruto",  # Matches POClaroOCRModel field name
        "label": "Subtotal (Base Gravable)",
        "compare_type": "numeric",
        "tolerance": 0.05
    },
    "total_iva": {
        "oc_field": "TotalIva",  # Matches POClaroOCRModel field name
        "label": "Total IVA",
        "compare_type": "numeric",
        "tolerance": 0.05
    },
}
