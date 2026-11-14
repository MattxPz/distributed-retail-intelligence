# Distributed Retail Intelligence

Monorepositorio previsto para simular una tienda con tres sucursales y diez productos: ventas transaccionales, inventario local, sincronización tras desconexiones y pronósticos de demanda. Cada sucursal será la autoridad de su stock; el sistema central mantendrá proyecciones eventualmente consistentes.

El MVP incluirá inventario inicial y ventas, sin pagos, devoluciones ni transferencias. La arquitectura prevista utiliza Python 3.12, FastAPI, SQLAlchemy, Alembic, SQLite por sucursal, PostgreSQL central, HTTPX, Streamlit y herramientas de análisis y aprendizaje automático. Véase [la arquitectura](docs/architecture.md) y [el progreso](docs/progress.md).

**Todavía no hay aplicación ejecutable.** La siguiente etapa es crearla con el **prompt 1**. Las versiones compatibles se resolverán y bloquearán cuando se cree el entorno.
