"""
Azure Blob Storage Service
Handles operations: list, download, extract ZIP files
"""
import os
import io
import zipfile
import logging
from typing import List, Optional, Dict, Any, BinaryIO
from datetime import datetime

from azure.storage.blob import BlobServiceClient, ContainerClient, BlobClient
from azure.core.exceptions import ResourceNotFoundError

logger = logging.getLogger(__name__)


class BlobService:
    """Service for Azure Blob Storage operations"""

    def __init__(
        self,
        connection_string: Optional[str] = None,
        container_name: Optional[str] = None
    ):
        """
        Initialize Blob Service

        Args:
            connection_string: Azure Storage connection string
            container_name: Container name (default: facturas)
        """
        self.connection_string = connection_string or os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        self.container_name = container_name or os.getenv('AZURE_STORAGE_CONTAINER_NAME', 'facturas')

        if not self.connection_string:
            logger.warning("No Azure Storage connection string provided - using mock mode")
            self._mock_mode = True
            self._mock_storage: Dict[str, bytes] = {}
        else:
            self._mock_mode = False
            self._blob_service_client = BlobServiceClient.from_connection_string(
                self.connection_string
            )
            self._container_client = self._blob_service_client.get_container_client(
                self.container_name
            )

    def _get_mock_invoices(self) -> List[Dict[str, Any]]:
        """Return mock invoice list for demo"""
        return [
            {
                "name": "SC14328.zip",
                "invoice_id": "SC14328",
                "path": "facturas/incoming/SC14328.zip",
                "size": 245000,
                "last_modified": datetime(2025, 5, 20, 10, 30, 0),
            },
            {
                "name": "SC14434.zip",
                "invoice_id": "SC14434",
                "path": "facturas/incoming/SC14434.zip",
                "size": 312000,
                "last_modified": datetime(2025, 6, 6, 14, 15, 0),
            },
            {
                "name": "SC14591.zip",
                "invoice_id": "SC14591",
                "path": "facturas/incoming/SC14591.zip",
                "size": 287000,
                "last_modified": datetime(2025, 6, 13, 9, 45, 0),
            },
        ]

    def list_invoices(self, prefix: str = "incoming/") -> List[Dict[str, Any]]:
        """
        List invoice ZIP files in blob storage

        Args:
            prefix: Blob prefix to filter (default: incoming/)

        Returns:
            List of invoice metadata dictionaries
        """
        if self._mock_mode:
            return self._get_mock_invoices()

        invoices = []
        try:
            blobs = self._container_client.list_blobs(name_starts_with=prefix)
            for blob in blobs:
                if blob.name.endswith('.zip'):
                    # Extract invoice ID from filename
                    filename = os.path.basename(blob.name)
                    invoice_id = filename.replace('.zip', '')

                    invoices.append({
                        "name": filename,
                        "invoice_id": invoice_id,
                        "path": blob.name,
                        "size": blob.size,
                        "last_modified": blob.last_modified,
                    })
        except Exception as e:
            logger.error(f"Error listing blobs: {e}")
            raise

        return invoices

    def download_blob(self, blob_path: str) -> bytes:
        """
        Download blob content

        Args:
            blob_path: Full path to blob

        Returns:
            Blob content as bytes
        """
        if self._mock_mode:
            if blob_path in self._mock_storage:
                return self._mock_storage[blob_path]
            raise ResourceNotFoundError(f"Blob not found: {blob_path}")

        try:
            blob_client = self._container_client.get_blob_client(blob_path)
            return blob_client.download_blob().readall()
        except ResourceNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error downloading blob {blob_path}: {e}")
            raise

    def download_blob_to_stream(self, blob_path: str) -> BinaryIO:
        """
        Download blob to stream

        Args:
            blob_path: Full path to blob

        Returns:
            BytesIO stream with blob content
        """
        content = self.download_blob(blob_path)
        return io.BytesIO(content)

    def upload_blob(
        self,
        blob_path: str,
        content: bytes,
        overwrite: bool = True
    ) -> str:
        """
        Upload content to blob

        Args:
            blob_path: Destination path
            content: Content as bytes
            overwrite: Whether to overwrite existing blob

        Returns:
            Full blob URL
        """
        if self._mock_mode:
            self._mock_storage[blob_path] = content
            return f"mock://{self.container_name}/{blob_path}"

        try:
            blob_client = self._container_client.get_blob_client(blob_path)
            blob_client.upload_blob(content, overwrite=overwrite)
            return blob_client.url
        except Exception as e:
            logger.error(f"Error uploading blob {blob_path}: {e}")
            raise

    def extract_zip(self, blob_path: str) -> Dict[str, bytes]:
        """
        Download and extract ZIP file from blob storage

        Args:
            blob_path: Path to ZIP file in blob storage

        Returns:
            Dictionary mapping filename to content bytes
        """
        zip_content = self.download_blob(blob_path)
        extracted_files = {}

        with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zip_ref:
            for file_info in zip_ref.filelist:
                # Skip directories
                if file_info.is_dir():
                    continue

                filename = os.path.basename(file_info.filename)
                # Skip hidden files
                if filename.startswith('.'):
                    continue

                content = zip_ref.read(file_info.filename)
                extracted_files[filename] = content
                logger.info(f"Extracted: {filename} ({len(content)} bytes)")

        return extracted_files

    def extract_and_save_zip(
        self,
        blob_path: str,
        destination_prefix: str = "extracted/"
    ) -> List[str]:
        """
        Extract ZIP and save files to blob storage

        Args:
            blob_path: Path to ZIP file
            destination_prefix: Prefix for extracted files

        Returns:
            List of paths to extracted files
        """
        extracted = self.extract_zip(blob_path)
        saved_paths = []

        # Get invoice ID from zip filename
        zip_filename = os.path.basename(blob_path)
        invoice_id = zip_filename.replace('.zip', '')

        for filename, content in extracted.items():
            dest_path = f"{destination_prefix}{invoice_id}/{filename}"
            self.upload_blob(dest_path, content)
            saved_paths.append(dest_path)
            logger.info(f"Saved extracted file: {dest_path}")

        return saved_paths

    def blob_exists(self, blob_path: str) -> bool:
        """Check if blob exists"""
        if self._mock_mode:
            return blob_path in self._mock_storage

        try:
            blob_client = self._container_client.get_blob_client(blob_path)
            return blob_client.exists()
        except Exception:
            return False

    def delete_blob(self, blob_path: str) -> bool:
        """Delete a blob"""
        if self._mock_mode:
            if blob_path in self._mock_storage:
                del self._mock_storage[blob_path]
                return True
            return False

        try:
            blob_client = self._container_client.get_blob_client(blob_path)
            blob_client.delete_blob()
            return True
        except ResourceNotFoundError:
            return False
        except Exception as e:
            logger.error(f"Error deleting blob {blob_path}: {e}")
            raise

    def delete_invoice(self, invoice_id: str) -> bool:
        """
        Delete all blobs related to an invoice (ZIP and extracted files)

        Args:
            invoice_id: Invoice ID

        Returns:
            True if any files were deleted
        """
        deleted_any = False

        # Delete ZIP from incoming/
        zip_path = f"incoming/{invoice_id}.zip"
        if self.delete_blob(zip_path):
            logger.info(f"Deleted ZIP: {zip_path}")
            deleted_any = True

        # Delete extracted files
        extracted_prefix = f"extracted/{invoice_id}/"
        if self._mock_mode:
            # In mock mode, delete all files with this prefix
            to_delete = [k for k in self._mock_storage.keys() if k.startswith(extracted_prefix)]
            for path in to_delete:
                del self._mock_storage[path]
                deleted_any = True
        else:
            try:
                blobs = self._container_client.list_blobs(name_starts_with=extracted_prefix)
                for blob in blobs:
                    self.delete_blob(blob.name)
                    logger.info(f"Deleted extracted file: {blob.name}")
                    deleted_any = True
            except Exception as e:
                logger.warning(f"Error deleting extracted files: {e}")

        return deleted_any

    def move_blob(self, source_path: str, destination_path: str) -> str:
        """
        Move blob from source to destination

        Args:
            source_path: Source blob path
            destination_path: Destination blob path

        Returns:
            New blob URL
        """
        content = self.download_blob(source_path)
        url = self.upload_blob(destination_path, content)
        self.delete_blob(source_path)
        return url

    def get_file_type(self, filename: str) -> str:
        """
        Determine file type from extension

        Args:
            filename: Name of the file

        Returns:
            File type string (xml, pdf, xlsx, unknown)
        """
        ext = os.path.splitext(filename)[1].lower()
        type_map = {
            '.xml': 'xml',
            '.pdf': 'pdf',
            '.xlsx': 'xlsx',
            '.xls': 'xls',
        }
        return type_map.get(ext, 'unknown')

    def categorize_extracted_files(
        self,
        files: Dict[str, bytes]
    ) -> Dict[str, List[str]]:
        """
        Categorize extracted files by type

        Args:
            files: Dictionary of filename to content

        Returns:
            Dictionary mapping type to list of filenames
        """
        categories = {
            'xml': [],
            'pdf': [],
            'xlsx': [],
            'other': [],
        }

        for filename in files.keys():
            file_type = self.get_file_type(filename)
            if file_type in categories:
                categories[file_type].append(filename)
            else:
                categories['other'].append(filename)

        return categories

    def extract_zip_with_nested(
        self,
        zip_content: bytes,
        invoice_id: str
    ) -> Dict[str, Any]:
        """
        Extract ZIP including nested ZIPs (Anexo.zip, adjuntos.zip).
        Returns ALL PDFs found (both main level and nested).

        Args:
            zip_content: ZIP file content as bytes
            invoice_id: Invoice ID for logging

        Returns:
            Dictionary with main_files, attachments (ALL PDFs), and nested_zip_name
        """
        result = {
            "main_files": [],      # XML, main PDF (factura)
            "attachments": [],      # ALL PDFs available for selection
            "nested_zip_name": None,
            "all_files": {}        # All extracted files as filename -> bytes
        }

        try:
            with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zf:
                for file_info in zf.namelist():
                    # Skip directories
                    if file_info.endswith('/'):
                        continue

                    filename = os.path.basename(file_info)
                    # Skip hidden files
                    if filename.startswith('.') or not filename:
                        continue

                    file_lower = filename.lower()

                    # Detect nested ZIP (Anexo.zip, adjuntos.zip)
                    if file_lower in ['anexo.zip', 'adjuntos.zip', 'attachments.zip']:
                        result["nested_zip_name"] = filename
                        nested_content = zf.read(file_info)

                        try:
                            with zipfile.ZipFile(io.BytesIO(nested_content), 'r') as nested_zf:
                                for nested_file in nested_zf.namelist():
                                    if nested_file.endswith('/'):
                                        continue

                                    nested_filename = os.path.basename(nested_file)
                                    if nested_filename.startswith('.') or not nested_filename:
                                        continue

                                    nested_file_content = nested_zf.read(nested_file)
                                    nested_file_size = len(nested_file_content)

                                    if nested_filename.lower().endswith('.pdf'):
                                        result["attachments"].append({
                                            "name": nested_filename,
                                            "content": nested_file_content,
                                            "size": nested_file_size,
                                            "source": "nested_zip"
                                        })
                                        result["all_files"][f"anexo/{nested_filename}"] = nested_file_content
                                    else:
                                        result["all_files"][f"anexo/{nested_filename}"] = nested_file_content

                                logger.info(f"Extracted {len(result['attachments'])} PDFs from nested ZIP: {filename}")
                        except zipfile.BadZipFile:
                            logger.warning(f"Could not extract nested ZIP: {filename}")

                    elif file_lower.endswith('.xml'):
                        content = zf.read(file_info)
                        result["main_files"].append({
                            "name": filename,
                            "content": content,
                            "type": "xml",
                            "size": len(content)
                        })
                        result["all_files"][filename] = content

                    elif file_lower.endswith('.pdf'):
                        content = zf.read(file_info)
                        result["main_files"].append({
                            "name": filename,
                            "content": content,
                            "type": "pdf",
                            "size": len(content)
                        })
                        result["all_files"][filename] = content
                        # Also add to attachments for selection
                        result["attachments"].append({
                            "name": filename,
                            "content": content,
                            "size": len(content),
                            "source": "main_zip"
                        })

                    else:
                        # Other files
                        content = zf.read(file_info)
                        result["all_files"][filename] = content

            logger.info(f"Invoice {invoice_id}: Extracted {len(result['main_files'])} main files, "
                       f"{len(result['attachments'])} total PDFs available")

        except zipfile.BadZipFile as e:
            logger.error(f"Invalid ZIP file for invoice {invoice_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error extracting ZIP for invoice {invoice_id}: {e}")
            raise

        return result

    def extract_zip_from_blob_with_nested(self, blob_path: str) -> Dict[str, Any]:
        """
        Download ZIP from blob and extract with nested ZIP support.

        Args:
            blob_path: Path to ZIP in blob storage

        Returns:
            Same as extract_zip_with_nested
        """
        invoice_id = os.path.basename(blob_path).replace('.zip', '')
        zip_content = self.download_blob(blob_path)
        return self.extract_zip_with_nested(zip_content, invoice_id)

    def extract_zip_from_local_file(self, file_path: str) -> Dict[str, Any]:
        """
        Extract ZIP from local file system (for demo purposes).

        Args:
            file_path: Path to local ZIP file

        Returns:
            Same as extract_zip_with_nested
        """
        invoice_id = os.path.basename(file_path).replace('.zip', '')
        with open(file_path, 'rb') as f:
            zip_content = f.read()
        return self.extract_zip_with_nested(zip_content, invoice_id)


# Singleton instance
_blob_service: Optional[BlobService] = None


def get_blob_service() -> BlobService:
    """Get or create BlobService singleton"""
    global _blob_service
    if _blob_service is None:
        _blob_service = BlobService()
    return _blob_service
