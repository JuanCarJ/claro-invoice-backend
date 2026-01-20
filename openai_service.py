"""
Azure OpenAI Service for Rules Chatbot
Handles natural language rule definition and invoice queries
"""
import os
import json
import logging
import re
from typing import Optional, Dict, Any, List

from models import (
    ChatMessage, ChatRequest, ChatResponse,
    ValidationRule, RuleCondition, RuleType
)

logger = logging.getLogger(__name__)

# Try to import Azure OpenAI
try:
    from openai import AzureOpenAI
    AZURE_OPENAI_AVAILABLE = True
except ImportError:
    AZURE_OPENAI_AVAILABLE = False
    logger.warning("Azure OpenAI SDK not available")


SYSTEM_PROMPT = """Eres un asistente experto en validación de facturas electrónicas colombianas (DIAN).
Tu rol es ayudar con consultas sobre facturas y órdenes de compra, así como definir reglas de validación.

IMPORTANTE: Por confidencialidad, NO tienes acceso a NIT ni nombres de empresas (proveedor/cliente).

=== DATOS DE LA FACTURA ===
{invoice_context}

=== DATOS COMPLETOS DE LA FACTURA (LÍNEAS DE DETALLE) ===
{invoice_lines}

=== DATOS COMPLETOS DE LA ORDEN DE COMPRA (OCR) ===
{oc_context}

=== CAMPOS SELECCIONADOS POR EL USUARIO ===
{selected_fields}

=== ESTADO DE VALIDACIÓN DE REGLAS ===
{validation_status}

=== DISCREPANCIAS (FACTURA vs OC) ===
{discrepancies}

=== REGLAS CONFIGURADAS ===
{existing_rules}

INSTRUCCIONES:
1. Cuando te pregunten sobre datos de la factura o la OC, responde con la información disponible arriba
2. Puedes responder preguntas sobre líneas de detalle, totales, impuestos, fechas, etc.
3. Si te piden crear una regla, usa el formato JSON indicado
4. Si te preguntan por inconsistencias, usa la sección de discrepancias
5. Siempre responde en español de forma clara y concisa

Formato para definir reglas:
```json
{{
  "id": "CUSTOM_XXX",
  "nombre": "Nombre corto descriptivo",
  "descripcion": "Descripción completa de la regla",
  "tipo": "blocking" o "warning",
  "condicion": {{
    "campo": "nombre_del_campo",
    "operador": ">" | "<" | "==" | "!=" | "contains" | "exists",
    "valor": "valor_a_comparar"
  }}
}}
```
"""


class OpenAIService:
    """Service for Azure OpenAI integration"""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        deployment: Optional[str] = None,
        api_version: Optional[str] = None
    ):
        """
        Initialize OpenAI Service

        Args:
            endpoint: Azure OpenAI endpoint
            api_key: API key
            deployment: Deployment name (model)
            api_version: API version
        """
        self.endpoint = endpoint or os.getenv('AZURE_OPENAI_ENDPOINT')
        self.api_key = api_key or os.getenv('AZURE_OPENAI_KEY')
        self.deployment = deployment or os.getenv('AZURE_OPENAI_DEPLOYMENT', 'gpt-4o')
        self.api_version = api_version or os.getenv('AZURE_OPENAI_API_VERSION', '2024-02-15-preview')

        self._mock_mode = False

        if not AZURE_OPENAI_AVAILABLE:
            logger.warning("Azure OpenAI SDK not installed - using mock mode")
            self._mock_mode = True
        elif not self.endpoint or not self.api_key:
            logger.warning("No Azure OpenAI credentials - using mock mode")
            self._mock_mode = True
        else:
            self._client = AzureOpenAI(
                azure_endpoint=self.endpoint,
                api_key=self.api_key,
                api_version=self.api_version,
            )

    def _format_invoice_context(self, invoice_data: Dict[str, Any]) -> str:
        """Format invoice data for context (excluding special keys and confidential data)"""
        if not invoice_data:
            return "No hay datos de factura cargados."

        lines = []
        for key, value in invoice_data.items():
            # Skip special keys (validation results, discrepancies) - handled separately
            if key.startswith('_'):
                continue
            if value is not None:
                lines.append(f"- {key}: {value}")
        return "\n".join(lines) if lines else "Sin datos disponibles."

    def _format_selected_fields(self, fields: List[str]) -> str:
        """Format selected fields list"""
        if not fields:
            return "Ninguno seleccionado"
        return ", ".join(fields)

    def _format_existing_rules(self, rules: List[ValidationRule]) -> str:
        """Format existing rules for context"""
        if not rules:
            return "Ninguna regla configurada aún."

        lines = []
        for rule in rules:
            # Handle both enum and string tipo
            tipo_str = rule.tipo.value if hasattr(rule.tipo, 'value') else str(rule.tipo)
            lines.append(f"- {rule.id}: {rule.nombre} ({tipo_str})")
        return "\n".join(lines)

    def _format_validation_status(self, invoice_data: Dict[str, Any]) -> str:
        """Format validation results for context"""
        validation_results = invoice_data.get('_validation_results', [])
        if not validation_results:
            return "No se ha ejecutado validación aún."

        passed = []
        failed = []
        for result in validation_results:
            rule_name = result.get('rule_name', 'Regla desconocida')
            status = result.get('status', 'unknown')
            message = result.get('message', '')

            if status == 'passed':
                passed.append(f"- {rule_name}: OK")
            elif status == 'failed':
                failed.append(f"- {rule_name}: FALLÓ - {message}")

        lines = []
        if passed:
            lines.append("Reglas que PASARON:")
            lines.extend(passed)
        if failed:
            lines.append("\nReglas que FALLARON:")
            lines.extend(failed)

        return "\n".join(lines) if lines else "Sin resultados de validación."

    def _format_discrepancies(self, invoice_data: Dict[str, Any]) -> str:
        """Format OC discrepancies for context"""
        discrepancies = invoice_data.get('_oc_discrepancies', [])
        if not discrepancies:
            return "No hay discrepancias detectadas o no se ha comparado con OC."

        lines = ["Discrepancias entre Factura XML y Orden de Compra:"]
        for disc in discrepancies:
            field = disc.get('field_label', disc.get('field_name', 'Campo'))
            xml_val = disc.get('xml_value', 'N/A')
            oc_val = disc.get('oc_value', 'N/A')
            lines.append(f"- {field}: Factura={xml_val} vs OC={oc_val}")

        return "\n".join(lines)

    def _format_invoice_lines(self, invoice_data: Dict[str, Any]) -> str:
        """Format full invoice data including lines"""
        full_invoice = invoice_data.get('_invoice_full', {})
        if not full_invoice:
            return "No hay datos de líneas disponibles."

        lines = []

        # Basic invoice info
        if full_invoice.get('invoice_number'):
            lines.append(f"Factura: {full_invoice.get('invoice_number')}")
        if full_invoice.get('cufe'):
            lines.append(f"CUFE: {full_invoice.get('cufe')}")
        if full_invoice.get('issue_date'):
            lines.append(f"Fecha emisión: {full_invoice.get('issue_date')}")
        if full_invoice.get('due_date'):
            lines.append(f"Fecha vencimiento: {full_invoice.get('due_date')}")
        if full_invoice.get('order_reference'):
            lines.append(f"Orden de Compra: {full_invoice.get('order_reference')}")
        if full_invoice.get('currency'):
            lines.append(f"Moneda: {full_invoice.get('currency')}")

        # Monetary totals
        monetary = full_invoice.get('monetary_total', {})
        if monetary:
            lines.append("\nTotales monetarios:")
            if monetary.get('line_extension_amount'):
                lines.append(f"  - Subtotal (sin IVA): ${monetary.get('line_extension_amount'):,.0f}")
            if monetary.get('tax_exclusive_amount'):
                lines.append(f"  - Total sin impuestos: ${monetary.get('tax_exclusive_amount'):,.0f}")
            if monetary.get('tax_inclusive_amount'):
                lines.append(f"  - Total con IVA: ${monetary.get('tax_inclusive_amount'):,.0f}")
            if monetary.get('payable_amount'):
                lines.append(f"  - Total a pagar: ${monetary.get('payable_amount'):,.0f}")

        if full_invoice.get('total_iva'):
            lines.append(f"IVA total: ${full_invoice.get('total_iva'):,.0f}")
        if full_invoice.get('total_retenciones'):
            lines.append(f"Retenciones: ${full_invoice.get('total_retenciones'):,.0f}")

        # Invoice lines
        invoice_lines = full_invoice.get('lines', [])
        if invoice_lines:
            lines.append(f"\nLíneas de detalle ({len(invoice_lines)} líneas):")
            for i, line in enumerate(invoice_lines, 1):
                desc = line.get('description', 'Sin descripción')[:50]
                qty = line.get('quantity', 0)
                unit = line.get('unit_code', '')
                price = line.get('price_amount', 0)
                total = line.get('line_amount', 0)
                lines.append(f"  {i}. {desc} | Cant: {qty} {unit} | Precio: ${price:,.0f} | Total: ${total:,.0f}")

        # Taxes
        taxes = full_invoice.get('taxes', [])
        if taxes:
            lines.append("\nImpuestos:")
            for tax in taxes:
                lines.append(f"  - {tax.get('id', 'IVA')} {tax.get('percent', 0)}%: ${tax.get('tax_amount', 0):,.0f}")

        # Withholding taxes
        wh_taxes = full_invoice.get('withholding_taxes', [])
        if wh_taxes:
            lines.append("\nRetenciones:")
            for tax in wh_taxes:
                lines.append(f"  - {tax.get('id', 'Ret')} {tax.get('percent', 0)}%: ${tax.get('tax_amount', 0):,.0f}")

        # Notes
        notes = full_invoice.get('notes', [])
        if notes:
            lines.append("\nNotas:")
            for note in notes[:3]:  # Limit to 3 notes
                lines.append(f"  - {note[:100]}")

        return "\n".join(lines) if lines else "Sin información de líneas."

    def _format_oc_context(self, invoice_data: Dict[str, Any]) -> str:
        """Format full OC data from OCR"""
        oc_full = invoice_data.get('_oc_full', {})
        if not oc_full:
            return "No hay datos de Orden de Compra disponibles."

        lines = ["Campos extraídos de la Orden de Compra (OCR):"]
        for field_name, field_data in oc_full.items():
            value = field_data.get('value', 'N/A')
            confidence = field_data.get('confidence', 0) * 100
            # Format numbers nicely
            if isinstance(value, (int, float)):
                value = f"${value:,.0f}" if abs(value) > 100 else value
            lines.append(f"  - {field_name}: {value} (conf: {confidence:.0f}%)")

        return "\n".join(lines) if len(lines) > 1 else "Sin datos de OC."

    def _extract_json_from_response(self, response: str) -> Optional[Dict[str, Any]]:
        """Extract JSON from response text"""
        # Try to find JSON in code block
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', response)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find JSON without code block
        json_match = re.search(r'\{[\s\S]*"id"[\s\S]*"condicion"[\s\S]*\}', response)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        return None

    def _mock_chat_response(
        self,
        message: str,
        invoice_data: Dict[str, Any]
    ) -> tuple[str, Optional[ValidationRule]]:
        """Generate mock response for demo"""
        message_lower = message.lower()

        # Query about inconsistencies/discrepancies
        if any(word in message_lower for word in ['inconsistencia', 'discrepancia', 'diferencia', 'error', 'problema']):
            discrepancies = invoice_data.get('_oc_discrepancies', [])
            if discrepancies:
                lines = ["**Inconsistencias detectadas entre la Factura y la Orden de Compra:**\n"]
                for disc in discrepancies:
                    field = disc.get('field_label', disc.get('field_name', 'Campo'))
                    xml_val = disc.get('xml_value', 'N/A')
                    oc_val = disc.get('oc_value', 'N/A')
                    lines.append(f"- **{field}**: Factura = `{xml_val}` vs OC = `{oc_val}`")
                lines.append(f"\n**Total:** {len(discrepancies)} inconsistencia(s) encontrada(s).")
                return ("\n".join(lines), None)
            else:
                return ("No se han detectado inconsistencias entre la factura y la Orden de Compra. ¿Ya cargaste y procesaste el documento de OC?", None)

        # Query about validation failures
        if any(word in message_lower for word in ['regla', 'validacion', 'validación', 'fallo', 'falló', 'fallaron', 'pasaron']):
            validation_results = invoice_data.get('_validation_results', [])
            if validation_results:
                passed = [r for r in validation_results if r.get('status') == 'passed']
                failed = [r for r in validation_results if r.get('status') == 'failed']

                lines = ["**Resultado de la validación de reglas:**\n"]

                if failed:
                    lines.append(f"**Reglas que FALLARON ({len(failed)}):**")
                    for r in failed:
                        lines.append(f"- ❌ **{r.get('rule_name')}**: {r.get('message', 'Sin detalle')}")

                if passed:
                    lines.append(f"\n**Reglas que PASARON ({len(passed)}):**")
                    for r in passed:
                        lines.append(f"- ✅ {r.get('rule_name')}")

                return ("\n".join(lines), None)
            else:
                return ("Aún no se ha ejecutado la validación de reglas. Puedes hacerlo en el Paso 3 (Validación y Envío a SAP).", None)

        # Query about totals
        if any(word in message_lower for word in ['total', 'monto', 'valor', 'pagar', 'subtotal']):
            # Helper to safely convert to float
            def safe_float(val):
                if val is None:
                    return None
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return None

            subtotal = safe_float(invoice_data.get('subtotal', invoice_data.get('line_extension_amount')))
            total_iva = safe_float(invoice_data.get('total_iva', invoice_data.get('tax_iva_valor')))
            total_con_iva = safe_float(invoice_data.get('total_con_iva', invoice_data.get('tax_inclusive_amount')))
            total_pagable = safe_float(invoice_data.get('total_pagable', invoice_data.get('payable_amount')))
            total_retenciones = safe_float(invoice_data.get('total_retenciones'))

            if subtotal or total_iva or total_pagable:
                lines = ["**Totales de la factura:**\n"]
                if subtotal:
                    lines.append(f"- **Subtotal (sin IVA):** ${subtotal:,.0f} COP")
                if total_iva:
                    lines.append(f"- **IVA:** ${total_iva:,.0f} COP")
                if total_con_iva:
                    lines.append(f"- **Total con IVA:** ${total_con_iva:,.0f} COP")
                if total_retenciones:
                    lines.append(f"- **Retenciones:** ${total_retenciones:,.0f} COP")
                if total_pagable:
                    lines.append(f"- **Total a Pagar:** ${total_pagable:,.0f} COP")
                return ("\n".join(lines), None)
            else:
                return ("No tengo información de los totales. ¿Ya seleccionaste una factura?", None)

        # Query about IVA specifically
        if 'iva' in message_lower and ('cuanto' in message_lower or 'cuánto' in message_lower or 'porcentaje' in message_lower):
            def safe_float(val):
                if val is None:
                    return None
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return None

            iva_valor = safe_float(invoice_data.get('total_iva', invoice_data.get('tax_iva_valor')))
            iva_pct = invoice_data.get('tax_iva_porcentaje', 19)
            if iva_valor:
                return (
                    f"El IVA de esta factura es de **${iva_valor:,.0f} COP** "
                    f"({iva_pct}% sobre la base gravable).",
                    None
                )
            else:
                return ("No tengo información del IVA. ¿Ya seleccionaste una factura?", None)

        # Query about invoice lines/detail
        if any(word in message_lower for word in ['línea', 'linea', 'detalle', 'producto', 'servicio', 'item', 'items']):
            full_invoice = invoice_data.get('_invoice_full', {})
            invoice_lines = full_invoice.get('lines', [])
            if invoice_lines:
                lines = [f"**Líneas de detalle de la factura ({len(invoice_lines)} líneas):**\n"]
                for i, line in enumerate(invoice_lines, 1):
                    desc = line.get('description', 'Sin descripción')
                    qty = line.get('quantity', 0)
                    unit = line.get('unit_code', '')
                    price = line.get('price_amount', 0)
                    total = line.get('line_amount', 0)
                    lines.append(f"{i}. **{desc[:60]}**")
                    lines.append(f"   Cantidad: {qty} {unit} | Precio: ${price:,.0f} | Total: ${total:,.0f}")
                return ("\n".join(lines), None)
            else:
                return ("No hay información de líneas de detalle disponible.", None)

        # Query about OC data
        if any(word in message_lower for word in ['orden de compra', 'oc ', 'compra', 'pedido']):
            oc_full = invoice_data.get('_oc_full', {})
            if oc_full:
                lines = ["**Datos de la Orden de Compra (extraídos por OCR):**\n"]
                for field_name, field_data in oc_full.items():
                    value = field_data.get('value', 'N/A')
                    confidence = field_data.get('confidence', 0) * 100
                    if isinstance(value, (int, float)) and abs(value) > 100:
                        value = f"${value:,.0f}"
                    lines.append(f"- **{field_name}:** {value} _(conf: {confidence:.0f}%)_")
                return ("\n".join(lines), None)
            else:
                orden_ref = invoice_data.get('orden_compra')
                if orden_ref:
                    return (f"La factura hace referencia a la **Orden de Compra: {orden_ref}**, pero no se ha procesado el documento PDF de la OC.", None)
                return ("No hay datos de Orden de Compra disponibles. ¿Ya cargaste el documento de OC?", None)

        # Query about invoice data/info
        if any(word in message_lower for word in ['datos', 'información', 'info', 'factura', 'número', 'fecha']):
            invoice_number = invoice_data.get('invoice_number')
            issue_date = invoice_data.get('issue_date')
            due_date = invoice_data.get('due_date')
            orden_compra = invoice_data.get('orden_compra')
            lines_count = invoice_data.get('lines_count')

            # Also try from full invoice data
            full_invoice = invoice_data.get('_invoice_full', {})
            if not invoice_number and full_invoice:
                invoice_number = full_invoice.get('invoice_number')
                issue_date = full_invoice.get('issue_date')
                due_date = full_invoice.get('due_date')
                orden_compra = full_invoice.get('order_reference')

            if invoice_number or issue_date:
                lines = ["**Información de la factura:**\n"]
                if invoice_number:
                    lines.append(f"- **Número de factura:** {invoice_number}")
                if issue_date:
                    lines.append(f"- **Fecha de emisión:** {issue_date}")
                if due_date:
                    lines.append(f"- **Fecha de vencimiento:** {due_date}")
                if orden_compra:
                    lines.append(f"- **Orden de Compra:** {orden_compra}")
                if lines_count:
                    lines.append(f"- **Líneas de detalle:** {lines_count}")

                # Add CUFE if available
                if full_invoice.get('cufe'):
                    lines.append(f"- **CUFE:** {full_invoice.get('cufe')[:30]}...")

                return ("\n".join(lines), None)
            else:
                return ("No hay datos de factura cargados. Por favor selecciona una factura de la lista o carga un XML.", None)

        # Rule about total > 100M
        if '100' in message_lower and ('millon' in message_lower or 'millones' in message_lower):
            rule = ValidationRule(
                id="CUSTOM_001",
                nombre="Cumplimiento para facturas >100M",
                descripcion="Si la factura supera 100 millones, requiere formato de cumplimiento",
                tipo=RuleType.BLOCKING,
                fuentes=["xml", "formato_cumplimiento"],
                condicion=RuleCondition(
                    campo="total_pagable",
                    operador=">",
                    valor=100000000
                ),
                is_custom=True
            )
            response = """Entendido. He creado una regla de validación:

**Regla:** Si el total de la factura supera 100 millones COP, debe existir un formato de cumplimiento adjunto.

```json
{
  "id": "CUSTOM_001",
  "nombre": "Cumplimiento para facturas >100M",
  "descripcion": "Si la factura supera 100 millones, requiere formato de cumplimiento",
  "tipo": "blocking",
  "condicion": {
    "campo": "total_pagable",
    "operador": ">",
    "valor": 100000000
  }
}
```

Esta regla es **bloqueante**, lo que significa que la factura no podrá enviarse a SAP si no cumple esta condición."""
            return response, rule

        # Rule about IVA percentage
        if 'verifica' in message_lower and 'iva' in message_lower:
            rule = ValidationRule(
                id="CUSTOM_002",
                nombre="IVA estándar 19%",
                descripcion="El porcentaje de IVA debe ser 19%",
                tipo=RuleType.WARNING,
                fuentes=["xml"],
                condicion=RuleCondition(
                    campo="tax_iva_porcentaje",
                    operador="==",
                    valor=19.0
                ),
                is_custom=True
            )
            response = """He creado una regla para verificar el IVA:

```json
{
  "id": "CUSTOM_002",
  "nombre": "IVA estándar 19%",
  "descripcion": "El porcentaje de IVA debe ser 19%",
  "tipo": "warning",
  "condicion": {
    "campo": "tax_iva_porcentaje",
    "operador": "==",
    "valor": 19.0
  }
}
```

Esta regla generará una **advertencia** si el IVA no es 19%."""
            return response, rule

        # NIT validation rule
        if 'nit' in message_lower and ('coinc' in message_lower or 'verif' in message_lower):
            rule = ValidationRule(
                id="CUSTOM_003",
                nombre="NIT proveedor consistente",
                descripcion="El NIT del proveedor debe coincidir en XML y documentos adjuntos",
                tipo=RuleType.BLOCKING,
                fuentes=["xml", "orden_compra"],
                condicion=RuleCondition(
                    campo="supplier_nit",
                    operador="==",
                    valor="oc_proveedor_nit"
                ),
                is_custom=True
            )
            response = """He creado una regla para validar consistencia del NIT:

```json
{
  "id": "CUSTOM_003",
  "nombre": "NIT proveedor consistente",
  "descripcion": "El NIT del proveedor debe coincidir en XML y documentos adjuntos",
  "tipo": "blocking",
  "condicion": {
    "campo": "supplier_nit",
    "operador": "==",
    "valor": "oc_proveedor_nit"
  }
}
```

Esta regla verificará que el NIT del proveedor sea consistente entre el XML y la Orden de Compra."""
            return response, rule

        # Explain rule failure
        if 'por qué' in message_lower or 'porque' in message_lower or 'falló' in message_lower:
            return (
                "La regla pudo fallar por las siguientes razones:\n\n"
                "1. **Datos incompletos**: Algún campo requerido no está presente\n"
                "2. **Valores no coincidentes**: Los valores entre documentos no concuerdan\n"
                "3. **Umbrales excedidos**: El valor supera los límites configurados\n\n"
                "Por favor, verifica los datos específicos en la vista de validación.",
                None
            )

        # Default response - show available data summary
        lines = ["Puedo ayudarte con la siguiente información:\n"]

        # Check what data is available
        has_invoice = bool(invoice_data.get('invoice_number') or invoice_data.get('_invoice_full'))
        has_invoice_lines = bool(invoice_data.get('_invoice_full', {}).get('lines'))
        has_oc_data = bool(invoice_data.get('_oc_full'))
        has_validation = bool(invoice_data.get('_validation_results'))
        has_discrepancies = bool(invoice_data.get('_oc_discrepancies'))

        if has_invoice:
            lines.append("✅ **Datos de factura** disponibles - pregúntame por totales, fechas, IVA, etc.")
            if has_invoice_lines:
                line_count = len(invoice_data.get('_invoice_full', {}).get('lines', []))
                lines.append(f"   ↳ **{line_count} líneas de detalle** - pregunta 'ver líneas de detalle'")
        else:
            lines.append("⚠️ No hay factura cargada - selecciona una factura primero")

        if has_oc_data:
            oc_fields = len(invoice_data.get('_oc_full', {}))
            lines.append(f"✅ **Datos de OC** disponibles ({oc_fields} campos) - pregunta 'ver datos de la OC'")
        else:
            lines.append("ℹ️ No hay datos de OC cargados (¿ya procesaste el documento?)")

        if has_discrepancies:
            disc_count = len(invoice_data.get('_oc_discrepancies', []))
            lines.append(f"✅ **{disc_count} inconsistencia(s)** detectadas - pregunta '¿qué inconsistencias tiene?'")
        elif has_oc_data:
            lines.append("✅ Sin inconsistencias detectadas entre factura y OC")
        else:
            lines.append("ℹ️ No se ha comparado con OC")

        if has_validation:
            val_results = invoice_data.get('_validation_results', [])
            failed = len([r for r in val_results if r.get('status') == 'failed'])
            passed = len([r for r in val_results if r.get('status') == 'passed'])
            lines.append(f"✅ **Validación ejecutada** - {passed} OK, {failed} fallaron - pregunta '¿qué reglas fallaron?'")
        else:
            lines.append("ℹ️ No se ha ejecutado validación aún")

        lines.append("\n**Ejemplos de preguntas:**")
        lines.append("- '¿Qué inconsistencias tiene esta factura?'")
        lines.append("- '¿Cuáles son los totales?'")
        lines.append("- 'Ver líneas de detalle'")
        lines.append("- 'Ver datos de la orden de compra'")
        lines.append("- '¿Qué reglas fallaron?'")

        return ("\n".join(lines), None)

    def chat(
        self,
        request: ChatRequest,
        invoice_data: Optional[Dict[str, Any]] = None,
        existing_rules: Optional[List[ValidationRule]] = None
    ) -> ChatResponse:
        """
        Process chat message and return response

        Args:
            request: Chat request with message and context
            invoice_data: Current invoice data for context
            existing_rules: Already configured rules

        Returns:
            ChatResponse with response text and optional rule
        """
        invoice_data = invoice_data or {}
        existing_rules = existing_rules or []

        logger.info(f"OpenAI chat called: mock_mode={self._mock_mode}, message_length={len(request.message)}")

        if self._mock_mode:
            try:
                response_text, rule = self._mock_chat_response(request.message, invoice_data)

                # Build conversation history - ensure all items are ChatMessage objects
                history = []
                for msg in request.conversation_history:
                    if isinstance(msg, dict):
                        history.append(ChatMessage(**msg))
                    else:
                        history.append(msg)
                history.append(ChatMessage(role="user", content=request.message))
                history.append(ChatMessage(role="assistant", content=response_text))

                return ChatResponse(
                    response=response_text,
                    rule=rule,
                    conversation_history=history,
                )
            except Exception as e:
                logger.error(f"Error in mock chat response: {e}", exc_info=True)
                raise

        try:
            # Build system prompt
            system_content = SYSTEM_PROMPT.format(
                invoice_context=self._format_invoice_context(invoice_data),
                invoice_lines=self._format_invoice_lines(invoice_data),
                oc_context=self._format_oc_context(invoice_data),
                selected_fields=self._format_selected_fields(request.selected_fields),
                existing_rules=self._format_existing_rules(existing_rules),
                validation_status=self._format_validation_status(invoice_data),
                discrepancies=self._format_discrepancies(invoice_data),
            )

            # Build messages
            messages = [{"role": "system", "content": system_content}]

            # Add conversation history
            for msg in request.conversation_history:
                if isinstance(msg, dict):
                    messages.append({
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", ""),
                    })
                else:
                    messages.append({
                        "role": msg.role,
                        "content": msg.content,
                    })

            # Add current message
            messages.append({
                "role": "user",
                "content": request.message,
            })

            logger.info(f"Calling Azure OpenAI: deployment={self.deployment}, messages_count={len(messages)}")

            # Call Azure OpenAI
            response = self._client.chat.completions.create(
                model=self.deployment,
                messages=messages,
                temperature=0.7,
                max_tokens=1000,
            )

            response_text = response.choices[0].message.content
            logger.info(f"Azure OpenAI response received: length={len(response_text)}")

            # Try to extract rule from response
            rule = None
            rule_json = self._extract_json_from_response(response_text)
            if rule_json:
                try:
                    # Convert JSON to ValidationRule
                    condicion = None
                    if "condicion" in rule_json:
                        cond = rule_json["condicion"]
                        if isinstance(cond, dict) and "campo" in cond:
                            condicion = RuleCondition(
                                campo=cond["campo"],
                                operador=cond.get("operador", "=="),
                                valor=cond.get("valor"),
                            )

                    rule = ValidationRule(
                        id=rule_json.get("id", f"CUSTOM_{len(existing_rules)+1:03d}"),
                        nombre=rule_json.get("nombre", "Regla personalizada"),
                        descripcion=rule_json.get("descripcion", ""),
                        tipo=RuleType(rule_json.get("tipo", "warning")),
                        fuentes=rule_json.get("fuentes", ["xml"]),
                        condicion=condicion,
                        is_custom=True,
                    )
                except Exception as e:
                    logger.warning(f"Failed to parse rule from response: {e}")

            # Build conversation history - ensure all items are ChatMessage objects
            history = []
            for msg in request.conversation_history:
                if isinstance(msg, dict):
                    history.append(ChatMessage(**msg))
                else:
                    history.append(msg)
            history.append(ChatMessage(role="user", content=request.message))
            history.append(ChatMessage(role="assistant", content=response_text))

            return ChatResponse(
                response=response_text,
                rule=rule,
                conversation_history=history,
            )

        except Exception as e:
            logger.error(f"Error in Azure OpenAI chat completion: {e}", exc_info=True)
            logger.warning("Falling back to mock mode due to Azure OpenAI error")

            # Fallback to mock mode
            try:
                response_text, rule = self._mock_chat_response(request.message, invoice_data)

                # Build conversation history
                history = []
                for msg in request.conversation_history:
                    if isinstance(msg, dict):
                        history.append(ChatMessage(**msg))
                    else:
                        history.append(msg)
                history.append(ChatMessage(role="user", content=request.message))
                history.append(ChatMessage(role="assistant", content=response_text))

                return ChatResponse(
                    response=response_text,
                    rule=rule,
                    conversation_history=history,
                )
            except Exception as fallback_error:
                logger.error(f"Fallback mock mode also failed: {fallback_error}", exc_info=True)
                raise


# Singleton instance
_openai_service: Optional[OpenAIService] = None


def get_openai_service() -> OpenAIService:
    """Get or create OpenAIService singleton"""
    global _openai_service
    if _openai_service is None:
        _openai_service = OpenAIService()
    return _openai_service
