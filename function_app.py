"""
Azure Functions Entry Point - FastAPI ASGI Wrapper
"""
import azure.functions as func
from main import app as fastapi_app

# Wrap FastAPI with Azure Functions ASGI middleware
app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)
