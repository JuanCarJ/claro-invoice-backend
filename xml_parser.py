"""
XML Parser for DIAN Electronic Invoices (UBL 2.1)
Extracts all fiscal data from Colombian electronic invoices
"""
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List
from datetime import date
from models import (
    XMLInvoiceData, PartyInfo, TaxDetail, InvoiceLine,
    OrderReference, MonetaryTotal, PaymentMeans, InvoiceAuthorization, InvoicePeriod,
    AttachmentReference
)
import re


# DIAN UBL 2.1 Namespaces
NAMESPACES = {
    'cbc': 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2',
    'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2',
    'ext': 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2',
    'sts': 'dian:gov:co:facturaelectronica:Structures-2-1',
    'ds': 'http://www.w3.org/2000/09/xmldsig#',
    'xades': 'http://uri.etsi.org/01903/v1.3.2#',
    'xades141': 'http://uri.etsi.org/01903/v1.4.1#',
}

# AttachedDocument namespace
AD_NAMESPACE = 'urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2'

# Tax scheme mappings
TAX_SCHEME_NAMES = {
    '01': 'IVA',
    '04': 'INC',
    '05': 'ReteIVA',
    '06': 'ReteRenta',
    '07': 'ReteICA',
    'ZZ': 'Otros',
}


def _find_text(element: ET.Element, path: str, default: str = "") -> str:
    """Find text content at XPath"""
    node = element.find(path, NAMESPACES)
    if node is not None and node.text:
        return node.text.strip()
    return default


def _find_float(element: ET.Element, path: str, default: float = 0.0) -> float:
    """Find float value at XPath"""
    text = _find_text(element, path)
    if text:
        try:
            return float(text.replace(',', ''))
        except ValueError:
            pass
    return default


def _parse_date(date_str: str) -> Optional[date]:
    """Parse date string to date object"""
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def _parse_party(party_element: ET.Element) -> PartyInfo:
    """Parse AccountingSupplierParty or AccountingCustomerParty"""
    party = party_element.find('cac:Party', NAMESPACES)
    if party is None:
        party = party_element

    # Get company identification
    party_identification = party.find('cac:PartyIdentification', NAMESPACES)
    company_id = ""
    if party_identification is not None:
        company_id = _find_text(party_identification, 'cbc:ID')

    # Get party tax scheme info
    party_tax_scheme = party.find('cac:PartyTaxScheme', NAMESPACES)
    tax_level_code = ""
    tax_scheme_id = ""
    registration_name = ""
    supplier_assigned_account_id = ""

    if party_tax_scheme is not None:
        registration_name = _find_text(party_tax_scheme, 'cbc:RegistrationName')
        company_id = company_id or _find_text(party_tax_scheme, 'cbc:CompanyID')
        tax_level_code = _find_text(party_tax_scheme, 'cbc:TaxLevelCode')
        tax_scheme = party_tax_scheme.find('cac:TaxScheme', NAMESPACES)
        if tax_scheme is not None:
            tax_scheme_id = _find_text(tax_scheme, 'cbc:ID')

    # Supplier assigned account ID (código proveedor en cliente)
    customer_party = party_element.find('cac:Party', NAMESPACES)
    if customer_party is not None:
        supplier_assigned_account_id = _find_text(
            customer_party,
            'cac:PartyIdentification/cbc:ID[@schemeID="31"]/../../cbc:SupplierAssignedAccountID'
        )
        # Try alternate path
        if not supplier_assigned_account_id:
            supplier_assigned_account_id = _find_text(party_element, './/cbc:SupplierAssignedAccountID')

    # Get party legal entity
    party_legal_entity = party.find('cac:PartyLegalEntity', NAMESPACES)
    if party_legal_entity is not None:
        registration_name = registration_name or _find_text(party_legal_entity, 'cbc:RegistrationName')

    # Get party name
    party_name = party.find('cac:PartyName', NAMESPACES)
    if party_name is not None and not registration_name:
        registration_name = _find_text(party_name, 'cbc:Name')

    # Get address info
    address = party.find('.//cac:Address', NAMESPACES) or party.find('.//cac:PhysicalLocation/cac:Address', NAMESPACES)
    address_line = ""
    city_name = ""
    department = ""
    country_code = ""

    if address is not None:
        address_line_elem = address.find('cac:AddressLine', NAMESPACES)
        if address_line_elem is not None:
            address_line = _find_text(address_line_elem, 'cbc:Line')
        city_name = _find_text(address, 'cbc:CityName')
        department = _find_text(address, 'cbc:CountrySubentity')
        country = address.find('cac:Country', NAMESPACES)
        if country is not None:
            country_code = _find_text(country, 'cbc:IdentificationCode')

    # Get contact info
    contact = party.find('cac:Contact', NAMESPACES)
    email = ""
    phone = ""
    if contact is not None:
        email = _find_text(contact, 'cbc:ElectronicMail')
        phone = _find_text(contact, 'cbc:Telephone')

    return PartyInfo(
        company_id=company_id,
        registration_name=registration_name,
        tax_level_code=tax_level_code if tax_level_code else None,
        tax_scheme_id=tax_scheme_id if tax_scheme_id else None,
        address_line=address_line if address_line else None,
        city_name=city_name if city_name else None,
        department=department if department else None,
        country_code=country_code if country_code else None,
        email=email if email else None,
        phone=phone if phone else None,
        supplier_assigned_account_id=supplier_assigned_account_id if supplier_assigned_account_id else None,
    )


def _parse_tax(tax_element: ET.Element) -> TaxDetail:
    """Parse TaxTotal or WithholdingTaxTotal"""
    tax_subtotal = tax_element.find('cac:TaxSubtotal', NAMESPACES)
    if tax_subtotal is None:
        tax_subtotal = tax_element

    taxable_amount = _find_float(tax_subtotal, 'cbc:TaxableAmount')
    tax_amount = _find_float(tax_subtotal, 'cbc:TaxAmount')

    # Get percentage
    tax_category = tax_subtotal.find('cac:TaxCategory', NAMESPACES)
    percentage = 0.0
    tax_scheme_id = ""

    if tax_category is not None:
        percentage = _find_float(tax_category, 'cbc:Percent')
        tax_scheme = tax_category.find('cac:TaxScheme', NAMESPACES)
        if tax_scheme is not None:
            tax_scheme_id = _find_text(tax_scheme, 'cbc:ID')

    tax_name = TAX_SCHEME_NAMES.get(tax_scheme_id, f"Tax-{tax_scheme_id}")

    return TaxDetail(
        tax_scheme_id=tax_scheme_id,
        tax_name=tax_name,
        taxable_amount=taxable_amount,
        tax_percentage=percentage,
        tax_amount=tax_amount,
    )


def _parse_invoice_line(line_element: ET.Element) -> InvoiceLine:
    """Parse InvoiceLine element"""
    line_id = _find_text(line_element, 'cbc:ID')

    # Quantity
    quantity_elem = line_element.find('cbc:InvoicedQuantity', NAMESPACES)
    quantity = 0.0
    unit_code = "EA"
    if quantity_elem is not None:
        quantity = float(quantity_elem.text) if quantity_elem.text else 0.0
        unit_code = quantity_elem.get('unitCode', 'EA')

    line_extension = _find_float(line_element, 'cbc:LineExtensionAmount')

    # Item info
    item = line_element.find('cac:Item', NAMESPACES)
    description = ""
    product_code = ""
    if item is not None:
        description = _find_text(item, 'cbc:Description')
        if not description:
            description = _find_text(item, 'cbc:Name')

        # Standard item identification (product code)
        std_item_id = item.find('cac:StandardItemIdentification', NAMESPACES)
        if std_item_id is not None:
            product_code = _find_text(std_item_id, 'cbc:ID')

        # Sellers item identification as fallback
        if not product_code:
            sellers_item = item.find('cac:SellersItemIdentification', NAMESPACES)
            if sellers_item is not None:
                product_code = _find_text(sellers_item, 'cbc:ID')

    # Price
    price_elem = line_element.find('cac:Price', NAMESPACES)
    unit_price = 0.0
    if price_elem is not None:
        unit_price = _find_float(price_elem, 'cbc:PriceAmount')

    # Line tax
    tax_total = line_element.find('cac:TaxTotal', NAMESPACES)
    tax_amount = None
    if tax_total is not None:
        tax_amount = _find_float(tax_total, 'cbc:TaxAmount')

    return InvoiceLine(
        line_id=line_id,
        description=description,
        quantity=quantity,
        unit_code=unit_code,
        unit_price=unit_price,
        line_extension_amount=line_extension,
        product_code=product_code if product_code else None,
        tax_amount=tax_amount,
    )


def _parse_monetary_total(total_element: ET.Element) -> MonetaryTotal:
    """Parse LegalMonetaryTotal element"""
    return MonetaryTotal(
        line_extension_amount=_find_float(total_element, 'cbc:LineExtensionAmount'),
        tax_exclusive_amount=_find_float(total_element, 'cbc:TaxExclusiveAmount'),
        tax_inclusive_amount=_find_float(total_element, 'cbc:TaxInclusiveAmount'),
        allowance_total_amount=_find_float(total_element, 'cbc:AllowanceTotalAmount'),
        charge_total_amount=_find_float(total_element, 'cbc:ChargeTotalAmount'),
        payable_amount=_find_float(total_element, 'cbc:PayableAmount'),
    )


def _extract_invoice_from_attached_document(xml_content: str) -> tuple[str, List[AttachmentReference]]:
    """
    Extract embedded Invoice XML from AttachedDocument format.
    DIAN AttachedDocument contains the full Invoice XML in a CDATA section
    inside the Attachment/ExternalReference/Description element.

    Also extracts references to attachments mentioned in the document.

    Returns:
        Tuple of (invoice_xml_content, attachment_references)
    """
    attachment_refs = []

    # Try to parse the AttachedDocument to extract attachment references
    try:
        root = ET.fromstring(xml_content)

        # Look for ParentDocumentID (usually the invoice number)
        parent_doc_id = _find_text(root, 'cbc:ParentDocumentID')

        # Look for additional document references
        for doc_ref in root.findall('.//cac:DocumentReference', NAMESPACES):
            doc_id = _find_text(doc_ref, 'cbc:ID')
            doc_type = _find_text(doc_ref, 'cbc:DocumentType')
            if doc_id:
                ref_type = "documento"
                if "orden" in doc_type.lower() or "oc" in doc_id.lower():
                    ref_type = "orden_compra"
                elif "contrato" in doc_type.lower():
                    ref_type = "contrato"
                attachment_refs.append(AttachmentReference(
                    reference_id=doc_id,
                    reference_type=ref_type,
                    description=doc_type if doc_type else None,
                ))

        # Look for additional references in the attachment section
        for attachment in root.findall('.//cac:Attachment', NAMESPACES):
            ext_ref = attachment.find('cac:ExternalReference', NAMESPACES)
            if ext_ref is not None:
                uri = _find_text(ext_ref, 'cbc:URI')
                doc_hash = _find_text(ext_ref, 'cbc:DocumentHash')
                mime_code = _find_text(ext_ref, 'cbc:MimeCode')
                if uri or doc_hash:
                    attachment_refs.append(AttachmentReference(
                        reference_id=uri or doc_hash[:20] if doc_hash else "unknown",
                        reference_type="archivo_adjunto",
                        description=f"MIME: {mime_code}" if mime_code else None,
                    ))
    except ET.ParseError:
        pass  # If parsing fails, continue with regex extraction

    # Look for Invoice XML embedded in CDATA or directly in Description
    # Pattern 1: CDATA section containing Invoice
    cdata_pattern = r'<!\[CDATA\[(.*?<Invoice[^>]*>.*?</Invoice>.*?)\]\]>'
    match = re.search(cdata_pattern, xml_content, re.DOTALL)
    if match:
        return match.group(1), attachment_refs

    # Pattern 2: Invoice directly in Description (escaped or not)
    invoice_pattern = r'(<Invoice[^>]*xmlns[^>]*>.*?</Invoice>)'
    match = re.search(invoice_pattern, xml_content, re.DOTALL)
    if match:
        return match.group(1), attachment_refs

    # If no embedded invoice found, return original content
    return xml_content, attachment_refs


def parse_dian_xml(xml_content: str, xml_path: Optional[str] = None) -> XMLInvoiceData:
    """
    Parse DIAN electronic invoice XML (UBL 2.1)
    Supports both direct Invoice XML and AttachedDocument wrapper format.

    Args:
        xml_content: XML content as string
        xml_path: Optional path to original XML file

    Returns:
        XMLInvoiceData with all extracted fiscal data
    """
    attachment_refs = []

    # Check if this is an AttachedDocument wrapper
    if 'AttachedDocument' in xml_content and 'urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2' in xml_content:
        # Extract the embedded Invoice XML and attachment references
        xml_content, attachment_refs = _extract_invoice_from_attached_document(xml_content)

    # Parse XML
    root = ET.fromstring(xml_content)

    # Invoice identification
    invoice_number = _find_text(root, 'cbc:ID')
    cufe = _find_text(root, 'cbc:UUID')
    issue_date_str = _find_text(root, 'cbc:IssueDate')
    issue_time = _find_text(root, 'cbc:IssueTime')
    due_date_str = _find_text(root, 'cbc:DueDate')
    currency_code = _find_text(root, 'cbc:DocumentCurrencyCode', 'COP')
    invoice_type_code = _find_text(root, 'cbc:InvoiceTypeCode', '01')
    line_count_numeric = _find_text(root, 'cbc:LineCountNumeric', '0')

    # Parse all notes
    notes = []
    for note_elem in root.findall('cbc:Note', NAMESPACES):
        if note_elem.text:
            notes.append(note_elem.text.strip())

    # Extract QR code URL from notes
    qr_code = None
    for note in notes:
        if 'catalogo-vpfe.dian.gov.co' in note or 'searchqr' in note:
            qr_match = re.search(r'(https?://[^\s]+searchqr[^\s]+)', note)
            if qr_match:
                qr_code = qr_match.group(1)
                break

    # Parse supplier (AccountingSupplierParty)
    supplier_elem = root.find('cac:AccountingSupplierParty', NAMESPACES)
    supplier = PartyInfo(company_id="", registration_name="")
    if supplier_elem is not None:
        supplier = _parse_party(supplier_elem)

    # Parse customer (AccountingCustomerParty)
    customer_elem = root.find('cac:AccountingCustomerParty', NAMESPACES)
    customer = PartyInfo(company_id="", registration_name="")
    if customer_elem is not None:
        customer = _parse_party(customer_elem)

    # Parse payment means
    payment_means_list = []
    for pm_elem in root.findall('cac:PaymentMeans', NAMESPACES):
        pm_id = _find_text(pm_elem, 'cbc:ID')
        pm_code = _find_text(pm_elem, 'cbc:PaymentMeansCode')
        pm_due_date = _find_text(pm_elem, 'cbc:PaymentDueDate')
        if pm_code:
            payment_means_list.append(PaymentMeans(
                payment_means_id=pm_id or "1",
                payment_means_code=pm_code,
                payment_due_date=_parse_date(pm_due_date),
            ))

    # Parse invoice period
    invoice_period = None
    period_elem = root.find('cac:InvoicePeriod', NAMESPACES)
    if period_elem is not None:
        period_start = _find_text(period_elem, 'cbc:StartDate')
        period_end = _find_text(period_elem, 'cbc:EndDate')
        period_desc = _find_text(period_elem, 'cbc:Description')
        invoice_period = InvoicePeriod(
            start_date=_parse_date(period_start),
            end_date=_parse_date(period_end),
            description=period_desc if period_desc else None,
        )

    # Parse taxes (TaxTotal)
    taxes = []
    for tax_total in root.findall('cac:TaxTotal', NAMESPACES):
        tax = _parse_tax(tax_total)
        if tax.tax_scheme_id:  # Only add if we got scheme ID
            taxes.append(tax)

    # Parse withholding taxes (WithholdingTaxTotal)
    withholding_taxes = []
    for wh_tax in root.findall('cac:WithholdingTaxTotal', NAMESPACES):
        tax = _parse_tax(wh_tax)
        if tax.tax_scheme_id:
            withholding_taxes.append(tax)

    # Parse monetary total
    monetary_total_elem = root.find('cac:LegalMonetaryTotal', NAMESPACES)
    monetary_total = MonetaryTotal(
        line_extension_amount=0,
        tax_exclusive_amount=0,
        tax_inclusive_amount=0,
        payable_amount=0,
    )
    if monetary_total_elem is not None:
        monetary_total = _parse_monetary_total(monetary_total_elem)

    # Parse prepaid payment
    prepaid_amount = 0.0
    prepaid_elem = root.find('cac:PrepaidPayment', NAMESPACES)
    if prepaid_elem is not None:
        prepaid_amount = _find_float(prepaid_elem, 'cbc:PaidAmount')

    # Parse allowance/charge totals
    total_discount = 0.0
    total_charges = 0.0
    for ac_elem in root.findall('cac:AllowanceCharge', NAMESPACES):
        charge_indicator = _find_text(ac_elem, 'cbc:ChargeIndicator', 'false')
        amount = _find_float(ac_elem, 'cbc:Amount')
        if charge_indicator.lower() == 'true':
            total_charges += amount
        else:
            total_discount += amount

    # Parse invoice lines
    lines = []
    for line_elem in root.findall('cac:InvoiceLine', NAMESPACES):
        line = _parse_invoice_line(line_elem)
        lines.append(line)

    # Parse order reference
    order_ref_elem = root.find('cac:OrderReference', NAMESPACES)
    order_reference = None
    if order_ref_elem is not None:
        order_id = _find_text(order_ref_elem, 'cbc:ID')
        sales_order_id = _find_text(order_ref_elem, 'cbc:SalesOrderID')
        if order_id:
            order_reference = OrderReference(
                order_id=order_id,
                sales_order_id=sales_order_id if sales_order_id else None,
            )
            # Add order reference as attachment reference for validation
            attachment_refs.append(AttachmentReference(
                reference_id=order_id,
                reference_type="orden_compra",
                description=f"Orden de Compra referenciada en factura",
            ))

    # Calculate total IVA (tax scheme 01)
    total_iva = sum(t.tax_amount for t in taxes if t.tax_scheme_id == "01")

    # Calculate total withholdings
    total_retenciones = sum(t.tax_amount for t in withholding_taxes)

    return XMLInvoiceData(
        invoice_number=invoice_number,
        cufe=cufe,
        issue_date=_parse_date(issue_date_str) or date.today(),
        issue_time=issue_time if issue_time else None,
        due_date=_parse_date(due_date_str),
        currency_code=currency_code,
        invoice_type_code=invoice_type_code,
        notes=notes,
        supplier=supplier,
        customer=customer,
        payment_means=payment_means_list,
        invoice_period=invoice_period,
        authorization=None,  # TODO: Parse from UBLExtensions if needed
        taxes=taxes,
        withholding_taxes=withholding_taxes,
        monetary_total=monetary_total,
        total_discount=total_discount,
        total_charges=total_charges,
        prepaid_amount=prepaid_amount,
        lines=lines,
        line_count=int(line_count_numeric) if line_count_numeric.isdigit() else len(lines),
        order_reference=order_reference,
        qr_code=qr_code,
        total_iva=total_iva,
        total_retenciones=total_retenciones,
        attachment_references=attachment_refs,
        raw_xml_path=xml_path,
    )


def parse_dian_xml_file(file_path: str) -> XMLInvoiceData:
    """
    Parse DIAN XML from file path

    Args:
        file_path: Path to XML file

    Returns:
        XMLInvoiceData with all extracted fiscal data
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        xml_content = f.read()
    return parse_dian_xml(xml_content, file_path)


def xml_to_dict(xml_data: XMLInvoiceData) -> Dict[str, Any]:
    """Convert XMLInvoiceData to dictionary for display/validation"""
    result = {
        # Invoice ID
        "invoice_number": xml_data.invoice_number,
        "cufe": xml_data.cufe,
        "issue_date": xml_data.issue_date.isoformat() if xml_data.issue_date else None,
        "issue_time": xml_data.issue_time,
        "due_date": xml_data.due_date.isoformat() if xml_data.due_date else None,
        "currency_code": xml_data.currency_code,
        "invoice_type_code": xml_data.invoice_type_code,

        # Supplier
        "supplier_nit": xml_data.supplier.company_id,
        "supplier_name": xml_data.supplier.registration_name,
        "supplier_tax_level": xml_data.supplier.tax_level_code,
        "supplier_address": xml_data.supplier.address_line,
        "supplier_city": xml_data.supplier.city_name,
        "supplier_department": xml_data.supplier.department,
        "supplier_email": xml_data.supplier.email,
        "supplier_phone": xml_data.supplier.phone,

        # Customer
        "customer_nit": xml_data.customer.company_id,
        "customer_name": xml_data.customer.registration_name,
        "customer_tax_level": xml_data.customer.tax_level_code,
        "customer_supplier_code": xml_data.customer.supplier_assigned_account_id,
        "customer_address": xml_data.customer.address_line,
        "customer_city": xml_data.customer.city_name,

        # Totals
        "subtotal": xml_data.monetary_total.line_extension_amount,
        "total_iva": xml_data.total_iva,
        "total_retenciones": xml_data.total_retenciones,
        "total_con_iva": xml_data.monetary_total.tax_inclusive_amount,
        "total_pagable": xml_data.monetary_total.payable_amount,
        "total_descuentos": xml_data.total_discount,
        "total_cargos": xml_data.total_charges,
        "prepago": xml_data.prepaid_amount,

        # Order reference
        "orden_compra": xml_data.order_reference.order_id if xml_data.order_reference else None,
        "sales_order": xml_data.order_reference.sales_order_id if xml_data.order_reference else None,

        # QR Code
        "qr_code": xml_data.qr_code,

        # Notes
        "notes": xml_data.notes,

        # Line count
        "line_count": xml_data.line_count,
    }

    # Add taxes detail
    result["taxes"] = [
        {
            "tax_scheme_id": tax.tax_scheme_id,
            "tax_name": tax.tax_name,
            "taxable_amount": tax.taxable_amount,
            "tax_percentage": tax.tax_percentage,
            "tax_amount": tax.tax_amount,
        }
        for tax in xml_data.taxes
    ]

    # Add withholding taxes detail
    result["withholding_taxes"] = [
        {
            "tax_scheme_id": tax.tax_scheme_id,
            "tax_name": tax.tax_name,
            "taxable_amount": tax.taxable_amount,
            "tax_percentage": tax.tax_percentage,
            "tax_amount": tax.tax_amount,
        }
        for tax in xml_data.withholding_taxes
    ]

    # Add flat tax fields for backward compatibility
    for tax in xml_data.taxes:
        key_prefix = f"tax_{tax.tax_name.lower()}"
        result[f"{key_prefix}_base"] = tax.taxable_amount
        result[f"{key_prefix}_porcentaje"] = tax.tax_percentage
        result[f"{key_prefix}_valor"] = tax.tax_amount

    # Add withholding taxes flat
    for tax in xml_data.withholding_taxes:
        key_prefix = f"retencion_{tax.tax_name.lower()}"
        result[f"{key_prefix}_base"] = tax.taxable_amount
        result[f"{key_prefix}_porcentaje"] = tax.tax_percentage
        result[f"{key_prefix}_valor"] = tax.tax_amount

    # Add invoice lines (items)
    result["items"] = [
        {
            "line_id": line.line_id,
            "description": line.description,
            "quantity": line.quantity,
            "unit_code": line.unit_code,
            "unit_price": line.unit_price,
            "line_extension_amount": line.line_extension_amount,
            "product_code": line.product_code,
            "tax_amount": line.tax_amount,
        }
        for line in xml_data.lines
    ]

    # Add attachment references
    result["attachment_references"] = [
        {
            "reference_id": ref.reference_id,
            "reference_type": ref.reference_type,
            "description": ref.description,
            "found_in_zip": ref.found_in_zip,
            "matched_filename": ref.matched_filename,
        }
        for ref in xml_data.attachment_references
    ]

    return result
