"""
Rules Engine for Invoice Validation
Simplified rules that work primarily with XML data
"""
import logging
from datetime import date, datetime
from typing import Optional, Dict, Any, List

from models import (
    ValidationRule, RuleCondition, RuleType, RuleStatus,
    RuleResult, ValidationResult, XMLInvoiceData,
    ProcessedDocument, DocumentType
)

logger = logging.getLogger(__name__)


# Simplified validation rules (XML-focused)
STATIC_RULES: List[ValidationRule] = [
    ValidationRule(
        id="R001",
        nombre="NIT Proveedor Válido",
        descripcion="NIT del emisor debe existir en maestro de proveedores",
        tipo=RuleType.BLOCKING,
        fuentes=["xml"],
    ),
    ValidationRule(
        id="R002",
        nombre="CUFE Presente",
        descripcion="La factura debe tener CUFE válido de la DIAN",
        tipo=RuleType.BLOCKING,
        fuentes=["xml"],
    ),
    ValidationRule(
        id="R003",
        nombre="Totales Consistentes",
        descripcion="Subtotal + IVA - Retenciones debe ser igual al Total a Pagar",
        tipo=RuleType.WARNING,
        fuentes=["xml"],
    ),
    ValidationRule(
        id="R004",
        nombre="IVA Correcto",
        descripcion="El porcentaje de IVA debe ser 19%",
        tipo=RuleType.WARNING,
        fuentes=["xml"],
    ),
    ValidationRule(
        id="R005",
        nombre="Referencia Orden de Compra",
        descripcion="La factura debe tener referencia a una Orden de Compra",
        tipo=RuleType.WARNING,
        fuentes=["xml"],
    ),
]

# Simulated master data (providers registered in SAP)
VALID_NITS = [
    "830099847",   # SOFTTEK RENOVATION SAS
    "900123456",   # Proveedor Demo 1
    "800999888",   # Proveedor Demo 2
    "860001022",   # Proveedor Demo 3
    "900555444",   # Proveedor Demo 4
]


class RulesEngine:
    """Engine for evaluating validation rules"""

    def __init__(self):
        """Initialize Rules Engine"""
        self.static_rules = STATIC_RULES.copy()

    def _evaluate_r001(self, xml_data: Optional[XMLInvoiceData]) -> RuleResult:
        """R001: NIT Proveedor válido"""
        rule = self.static_rules[0]

        if not xml_data:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.SKIPPED,
                message="Sin datos XML para validar",
            )

        nit = xml_data.supplier.company_id
        if nit in VALID_NITS:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.PASSED,
                message=f"NIT {nit} ({xml_data.supplier.registration_name}) registrado en SAP",
                details={"nit": nit, "nombre": xml_data.supplier.registration_name},
            )
        else:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.FAILED,
                message=f"NIT {nit} NO encontrado en maestro de proveedores SAP",
                details={"nit": nit, "valid_nits": VALID_NITS},
            )

    def _evaluate_r002(self, xml_data: Optional[XMLInvoiceData]) -> RuleResult:
        """R002: CUFE Presente"""
        rule = self.static_rules[1]

        if not xml_data:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.SKIPPED,
                message="Sin datos XML para validar",
            )

        cufe = xml_data.cufe
        if cufe and len(cufe) > 10:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.PASSED,
                message=f"CUFE válido presente: {cufe[:20]}...",
                details={"cufe": cufe},
            )
        else:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.FAILED,
                message="CUFE no encontrado o inválido",
            )

    def _evaluate_r003(self, xml_data: Optional[XMLInvoiceData]) -> RuleResult:
        """R003: Totales Consistentes"""
        rule = self.static_rules[2]

        if not xml_data:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.SKIPPED,
                message="Sin datos XML para validar",
            )

        subtotal = xml_data.monetary_total.line_extension_amount
        total_payable = xml_data.monetary_total.payable_amount

        # Calculate IVA from taxes
        total_iva = sum(t.tax_amount for t in xml_data.taxes if t.tax_scheme_id == "01")

        # Calculate retentions from withholding_taxes
        total_retenciones = sum(t.tax_amount for t in xml_data.withholding_taxes)

        calculated_total = subtotal + total_iva - total_retenciones

        # Allow 1% tolerance
        tolerance = total_payable * 0.01
        if abs(calculated_total - total_payable) <= tolerance:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.PASSED,
                message=f"Totales consistentes: ${subtotal:,.0f} + ${total_iva:,.0f} - ${total_retenciones:,.0f} = ${total_payable:,.0f}",
                details={
                    "subtotal": subtotal,
                    "iva": total_iva,
                    "retenciones": total_retenciones,
                    "total_calculado": calculated_total,
                    "total_factura": total_payable,
                },
            )
        else:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.FAILED,
                message=f"Totales inconsistentes: Calculado ${calculated_total:,.0f} vs Factura ${total_payable:,.0f}",
                details={
                    "subtotal": subtotal,
                    "iva": total_iva,
                    "retenciones": total_retenciones,
                    "total_calculado": calculated_total,
                    "total_factura": total_payable,
                    "diferencia": abs(calculated_total - total_payable),
                },
            )

    def _evaluate_r004(self, xml_data: Optional[XMLInvoiceData]) -> RuleResult:
        """R004: IVA Correcto (19%)"""
        rule = self.static_rules[3]

        if not xml_data or not xml_data.taxes:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.SKIPPED,
                message="Sin información de impuestos en XML",
            )

        # Find IVA (tax scheme 01)
        iva_tax = next((t for t in xml_data.taxes if t.tax_scheme_id == "01"), None)

        if not iva_tax:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.SKIPPED,
                message="No se encontró IVA en el XML",
            )

        if iva_tax.tax_percentage == 19.0:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.PASSED,
                message=f"IVA correcto: {iva_tax.tax_percentage}% (${iva_tax.tax_amount:,.0f})",
                details={"iva_percent": iva_tax.tax_percentage, "iva_amount": iva_tax.tax_amount},
            )
        else:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.FAILED,
                message=f"IVA es {iva_tax.tax_percentage}% (esperado: 19%)",
                details={"iva_percent": iva_tax.tax_percentage, "expected": 19.0},
            )

    def _evaluate_r005(self, xml_data: Optional[XMLInvoiceData]) -> RuleResult:
        """R005: Referencia OC presente"""
        rule = self.static_rules[4]

        if not xml_data:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.SKIPPED,
                message="Sin datos XML para validar",
            )

        if xml_data.order_reference and xml_data.order_reference.order_id:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.PASSED,
                message=f"Orden de Compra referenciada: {xml_data.order_reference.order_id}",
                details={"orden_compra": xml_data.order_reference.order_id},
            )
        else:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.FAILED,
                message="No se encontró referencia a Orden de Compra",
            )

    def _evaluate_custom_rule(
        self,
        rule: ValidationRule,
        data: Dict[str, Any]
    ) -> RuleResult:
        """Evaluate a custom rule"""
        if not rule.condicion:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.SKIPPED,
                message="Regla sin condición definida",
            )

        field_value = data.get(rule.condicion.campo)

        if field_value is None:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.SKIPPED,
                message=f"Campo '{rule.condicion.campo}' no encontrado",
            )

        result = self._compare_values(
            field_value,
            rule.condicion.operador,
            rule.condicion.valor
        )

        if result:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.PASSED,
                message=f"Condición cumplida: {rule.condicion.campo} {rule.condicion.operador} {rule.condicion.valor}",
            )
        else:
            return RuleResult(
                rule_id=rule.id,
                rule_name=rule.nombre,
                status=RuleStatus.FAILED,
                message=f"Condición NO cumplida: {field_value} {rule.condicion.operador} {rule.condicion.valor}",
                details={
                    "field": rule.condicion.campo,
                    "actual_value": field_value,
                    "expected": f"{rule.condicion.operador} {rule.condicion.valor}",
                },
            )

    def _compare_values(self, value1: Any, operator: str, value2: Any) -> bool:
        """Compare two values with operator"""
        try:
            if value1 is None:
                return operator == "!=" and value2 is not None

            if operator == "exists":
                return value1 is not None and str(value1).strip() != ""

            if operator == "contains":
                return str(value2).lower() in str(value1).lower()

            if operator in [">", "<", ">=", "<=", "==", "!="]:
                try:
                    num1 = float(value1) if not isinstance(value1, (int, float)) else value1
                    num2 = float(value2) if not isinstance(value2, (int, float)) else value2

                    if operator == ">":
                        return num1 > num2
                    elif operator == "<":
                        return num1 < num2
                    elif operator == ">=":
                        return num1 >= num2
                    elif operator == "<=":
                        return num1 <= num2
                    elif operator == "==":
                        return num1 == num2
                    elif operator == "!=":
                        return num1 != num2
                except (ValueError, TypeError):
                    if operator == "==":
                        return str(value1) == str(value2)
                    elif operator == "!=":
                        return str(value1) != str(value2)

            return False
        except Exception as e:
            logger.warning(f"Error comparing values: {e}")
            return False

    def validate(
        self,
        invoice_id: str,
        xml_data: Optional[XMLInvoiceData] = None,
        documents: Optional[List[ProcessedDocument]] = None,
        custom_rules: Optional[List[ValidationRule]] = None,
        flat_data: Optional[Dict[str, Any]] = None
    ) -> ValidationResult:
        """
        Run all validation rules

        Args:
            invoice_id: Invoice identifier
            xml_data: Parsed XML data
            documents: Processed PDF documents (optional)
            custom_rules: Custom rules from chatbot
            flat_data: Flattened data dictionary for custom rules

        Returns:
            ValidationResult with all rule results
        """
        documents = documents or []
        custom_rules = custom_rules or []
        flat_data = flat_data or {}

        results = []

        # Static rules (5 rules)
        results.append(self._evaluate_r001(xml_data))
        results.append(self._evaluate_r002(xml_data))
        results.append(self._evaluate_r003(xml_data))
        results.append(self._evaluate_r004(xml_data))
        results.append(self._evaluate_r005(xml_data))

        # Custom rules
        for rule in custom_rules:
            result = self._evaluate_custom_rule(rule, flat_data)
            results.append(result)

        # Count results
        blocking_failures = sum(
            1 for i, r in enumerate(results)
            if r.status == RuleStatus.FAILED
            and (
                (i < len(self.static_rules) and self.static_rules[i].tipo == RuleType.BLOCKING)
                or (i >= len(self.static_rules) and custom_rules[i - len(self.static_rules)].tipo == RuleType.BLOCKING)
            )
        )

        warnings = sum(
            1 for i, r in enumerate(results)
            if r.status == RuleStatus.FAILED
            and (
                (i < len(self.static_rules) and self.static_rules[i].tipo == RuleType.WARNING)
                or (i >= len(self.static_rules) and custom_rules[i - len(self.static_rules)].tipo == RuleType.WARNING)
            )
        )

        passed = sum(1 for r in results if r.status == RuleStatus.PASSED)

        return ValidationResult(
            invoice_id=invoice_id,
            timestamp=datetime.now(),
            results=results,
            blocking_failures=blocking_failures,
            warnings=warnings,
            passed=passed,
            can_submit=blocking_failures == 0,
        )

    def get_all_rules(
        self,
        custom_rules: Optional[List[ValidationRule]] = None
    ) -> List[Dict[str, Any]]:
        """Get all rules (static + custom) for display"""
        custom_rules = custom_rules or []

        all_rules = []
        for rule in self.static_rules:
            all_rules.append({
                "id": rule.id,
                "nombre": rule.nombre,
                "descripcion": rule.descripcion,
                "tipo": rule.tipo.value,
                "fuentes": rule.fuentes,
                "is_custom": False,
            })

        for rule in custom_rules:
            all_rules.append({
                "id": rule.id,
                "nombre": rule.nombre,
                "descripcion": rule.descripcion,
                "tipo": rule.tipo.value,
                "fuentes": rule.fuentes,
                "is_custom": True,
                "condicion": {
                    "campo": rule.condicion.campo,
                    "operador": rule.condicion.operador,
                    "valor": rule.condicion.valor,
                } if rule.condicion else None,
            })

        return all_rules


# Singleton instance
_rules_engine: Optional[RulesEngine] = None


def get_rules_engine() -> RulesEngine:
    """Get or create RulesEngine singleton"""
    global _rules_engine
    if _rules_engine is None:
        _rules_engine = RulesEngine()
    return _rules_engine
