"""
Excel Processor using Pandas
Handles Mano de Obra and Fabricantes Excel files
"""
import io
import logging
from typing import Optional, Dict, Any, List, Tuple

import pandas as pd

from models import (
    DocumentType, ManoObraRow, ManoObraData,
    FabricanteRow, FabricantesData
)

logger = logging.getLogger(__name__)


class ExcelProcessor:
    """Process Excel files using Pandas"""

    def __init__(self):
        """Initialize Excel Processor"""
        pass

    def detect_excel_type(self, df: pd.DataFrame) -> DocumentType:
        """
        Detect type of Excel file based on column names

        Args:
            df: Pandas DataFrame

        Returns:
            DocumentType (EXCEL_MANO_OBRA, EXCEL_FABRICANTES, or UNKNOWN)
        """
        columns_lower = [col.lower().strip() for col in df.columns]

        # Mano de obra indicators
        mano_obra_indicators = ['empleado', 'cargo', 'horas', 'tarifa', 'trabajador', 'personal']
        # Fabricantes indicators
        fabricantes_indicators = ['codigo', 'fabricante', 'material', 'equipo', 'descripcion', 'cantidad']

        mano_obra_score = sum(
            1 for ind in mano_obra_indicators
            if any(ind in col for col in columns_lower)
        )
        fabricantes_score = sum(
            1 for ind in fabricantes_indicators
            if any(ind in col for col in columns_lower)
        )

        if mano_obra_score > fabricantes_score:
            return DocumentType.EXCEL_MANO_OBRA
        elif fabricantes_score > 0:
            return DocumentType.EXCEL_FABRICANTES
        else:
            return DocumentType.UNKNOWN

    def _normalize_column_names(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize column names (lowercase, strip whitespace)

        Args:
            df: Input DataFrame

        Returns:
            DataFrame with normalized column names
        """
        df.columns = [col.lower().strip() for col in df.columns]
        return df

    def _find_column(self, df: pd.DataFrame, possible_names: List[str]) -> Optional[str]:
        """
        Find column by possible names

        Args:
            df: DataFrame
            possible_names: List of possible column names

        Returns:
            Actual column name or None
        """
        columns_lower = {col.lower().strip(): col for col in df.columns}
        for name in possible_names:
            name_lower = name.lower().strip()
            if name_lower in columns_lower:
                return columns_lower[name_lower]
            # Check partial match
            for col_lower, col_orig in columns_lower.items():
                if name_lower in col_lower:
                    return col_orig
        return None

    def _clean_numeric(self, value: Any) -> float:
        """Clean and convert value to float"""
        if pd.isna(value):
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        # Try to parse string
        try:
            # Remove currency symbols and thousands separators
            cleaned = str(value).replace('$', '').replace(',', '').replace('.', '').strip()
            # Handle Colombian format (. for thousands, , for decimals)
            if cleaned:
                return float(cleaned)
        except ValueError:
            pass
        return 0.0

    def process_mano_obra(
        self,
        content: bytes,
        filename: str
    ) -> ManoObraData:
        """
        Process Mano de Obra Excel file

        Args:
            content: Excel file content
            filename: File name

        Returns:
            ManoObraData with parsed rows
        """
        try:
            df = pd.read_excel(io.BytesIO(content))

            # Find columns
            empleado_col = self._find_column(df, ['empleado', 'nombre', 'trabajador', 'personal'])
            cargo_col = self._find_column(df, ['cargo', 'puesto', 'rol', 'posicion'])
            horas_col = self._find_column(df, ['horas', 'hrs', 'tiempo'])
            tarifa_col = self._find_column(df, ['tarifa', 'tarifa/hora', 'tarifa_hora', 'valor hora', 'valor_hora'])
            total_col = self._find_column(df, ['total', 'valor total', 'subtotal'])

            if not empleado_col:
                logger.warning(f"Could not find employee column in {filename}")
                empleado_col = df.columns[0] if len(df.columns) > 0 else None

            rows = []
            total_horas = 0.0
            total_valor = 0.0

            for idx, row in df.iterrows():
                # Skip empty rows or total rows
                if empleado_col:
                    empleado = str(row.get(empleado_col, '')).strip()
                    if not empleado or empleado.upper() in ['TOTAL', 'SUBTOTAL', 'SUMA', '']:
                        continue
                    if pd.isna(row.get(empleado_col)):
                        continue

                horas = self._clean_numeric(row.get(horas_col, 0)) if horas_col else 0
                tarifa = self._clean_numeric(row.get(tarifa_col, 0)) if tarifa_col else 0
                total = self._clean_numeric(row.get(total_col, 0)) if total_col else (horas * tarifa)

                # Skip if all zeros
                if horas == 0 and tarifa == 0 and total == 0:
                    continue

                mano_obra_row = ManoObraRow(
                    empleado=empleado if empleado_col else f"Empleado {idx}",
                    cargo=str(row.get(cargo_col, 'N/A')).strip() if cargo_col else 'N/A',
                    horas=horas,
                    tarifa_hora=tarifa,
                    total=total if total > 0 else (horas * tarifa),
                )
                rows.append(mano_obra_row)
                total_horas += mano_obra_row.horas
                total_valor += mano_obra_row.total

            return ManoObraData(
                rows=rows,
                total_horas=total_horas,
                total_valor=total_valor,
            )

        except Exception as e:
            logger.error(f"Error processing mano de obra Excel {filename}: {e}")
            raise

    def process_fabricantes(
        self,
        content: bytes,
        filename: str
    ) -> FabricantesData:
        """
        Process Fabricantes Excel file

        Args:
            content: Excel file content
            filename: File name

        Returns:
            FabricantesData with parsed rows
        """
        try:
            df = pd.read_excel(io.BytesIO(content))

            # Find columns
            codigo_col = self._find_column(df, ['codigo', 'código', 'cod', 'id', 'referencia'])
            descripcion_col = self._find_column(df, ['descripcion', 'descripción', 'detalle', 'item', 'nombre'])
            fabricante_col = self._find_column(df, ['fabricante', 'marca', 'proveedor', 'manufacturer'])
            cantidad_col = self._find_column(df, ['cantidad', 'cant', 'qty', 'unidades'])
            valor_unit_col = self._find_column(df, ['valor unitario', 'valor_unitario', 'precio', 'valor unit', 'precio unitario'])
            total_col = self._find_column(df, ['total', 'valor total', 'subtotal', 'valor'])

            rows = []
            total_valor = 0.0

            for idx, row in df.iterrows():
                # Skip empty rows or total rows
                if codigo_col:
                    codigo = str(row.get(codigo_col, '')).strip()
                    if codigo.upper() in ['TOTAL', 'SUBTOTAL', 'SUMA', '']:
                        continue
                    if pd.isna(row.get(codigo_col)):
                        continue

                descripcion = str(row.get(descripcion_col, 'N/A')).strip() if descripcion_col else 'N/A'
                if descripcion.upper() in ['TOTAL', 'SUBTOTAL']:
                    continue

                cantidad = self._clean_numeric(row.get(cantidad_col, 0)) if cantidad_col else 0
                valor_unit = self._clean_numeric(row.get(valor_unit_col, 0)) if valor_unit_col else 0
                total = self._clean_numeric(row.get(total_col, 0)) if total_col else (cantidad * valor_unit)

                # Skip if all zeros
                if cantidad == 0 and valor_unit == 0 and total == 0:
                    continue

                fab_row = FabricanteRow(
                    codigo=codigo if codigo_col else f"ITEM{idx:03d}",
                    descripcion=descripcion,
                    fabricante=str(row.get(fabricante_col, 'N/A')).strip() if fabricante_col else 'N/A',
                    cantidad=cantidad,
                    valor_unitario=valor_unit,
                    total=total if total > 0 else (cantidad * valor_unit),
                )
                rows.append(fab_row)
                total_valor += fab_row.total

            return FabricantesData(
                rows=rows,
                total_valor=total_valor,
            )

        except Exception as e:
            logger.error(f"Error processing fabricantes Excel {filename}: {e}")
            raise

    def process_excel(
        self,
        content: bytes,
        filename: str,
        document_type_hint: Optional[DocumentType] = None
    ) -> Tuple[DocumentType, Any]:
        """
        Process Excel file and detect type automatically

        Args:
            content: Excel content
            filename: File name
            document_type_hint: Optional type hint

        Returns:
            Tuple of (DocumentType, parsed data)
        """
        try:
            df = pd.read_excel(io.BytesIO(content))

            if document_type_hint:
                doc_type = document_type_hint
            else:
                doc_type = self.detect_excel_type(df)

            if doc_type == DocumentType.EXCEL_MANO_OBRA:
                data = self.process_mano_obra(content, filename)
            elif doc_type == DocumentType.EXCEL_FABRICANTES:
                data = self.process_fabricantes(content, filename)
            else:
                # Try both and return the one with more rows
                try:
                    mano_obra = self.process_mano_obra(content, filename)
                except Exception:
                    mano_obra = ManoObraData(rows=[], total_horas=0, total_valor=0)

                try:
                    fabricantes = self.process_fabricantes(content, filename)
                except Exception:
                    fabricantes = FabricantesData(rows=[], total_valor=0)

                if len(mano_obra.rows) >= len(fabricantes.rows):
                    doc_type = DocumentType.EXCEL_MANO_OBRA
                    data = mano_obra
                else:
                    doc_type = DocumentType.EXCEL_FABRICANTES
                    data = fabricantes

            return doc_type, data

        except Exception as e:
            logger.error(f"Error processing Excel {filename}: {e}")
            raise

    def get_excel_summary(
        self,
        doc_type: DocumentType,
        data: Any
    ) -> Dict[str, Any]:
        """
        Get summary of processed Excel for display

        Args:
            doc_type: Document type
            data: Parsed data (ManoObraData or FabricantesData)

        Returns:
            Summary dictionary
        """
        if doc_type == DocumentType.EXCEL_MANO_OBRA:
            return {
                "type": "mano_obra",
                "type_display": "Mano de Obra",
                "row_count": len(data.rows),
                "total_horas": data.total_horas,
                "total_valor": data.total_valor,
                "rows": [
                    {
                        "empleado": row.empleado,
                        "cargo": row.cargo,
                        "horas": row.horas,
                        "tarifa_hora": row.tarifa_hora,
                        "total": row.total,
                    }
                    for row in data.rows
                ]
            }
        elif doc_type == DocumentType.EXCEL_FABRICANTES:
            return {
                "type": "fabricantes",
                "type_display": "Fabricantes/Materiales",
                "row_count": len(data.rows),
                "total_valor": data.total_valor,
                "rows": [
                    {
                        "codigo": row.codigo,
                        "descripcion": row.descripcion,
                        "fabricante": row.fabricante,
                        "cantidad": row.cantidad,
                        "valor_unitario": row.valor_unitario,
                        "total": row.total,
                    }
                    for row in data.rows
                ]
            }
        else:
            return {
                "type": "unknown",
                "type_display": "Desconocido",
                "row_count": 0,
                "rows": [],
            }


# Singleton instance
_excel_processor: Optional[ExcelProcessor] = None


def get_excel_processor() -> ExcelProcessor:
    """Get or create ExcelProcessor singleton"""
    global _excel_processor
    if _excel_processor is None:
        _excel_processor = ExcelProcessor()
    return _excel_processor
