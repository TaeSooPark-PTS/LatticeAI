"""Service layer extracted from server_app.py.

Services wrap stores and business logic with no dependency on the FastAPI app
object, so API routers (latticeai.api.*) and the app assembly
(latticeai.server_app) can import them without creating import cycles.
"""
