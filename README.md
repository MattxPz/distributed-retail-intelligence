# Distributed Retail Intelligence

Tienda simulada de tres sucursales y diez productos. Cada sucursal es autoridad
sobre su stock; el central conserva proyecciones eventualmente consistentes. El
MVP excluye pagos, devoluciones y transferencias.

Implementado: API de estado, panel, persistencia local/central, migraciones
separadas Alembic y seed idempotente. Cada sucursal tiene 100 unidades iniciales
por producto y su snapshot pendiente en outbox. Las tablas de ventas existen,
pero todavía no hay API de ventas ni publicador HTTP ni pronósticos.

Consulte [arquitectura](docs/architecture.md), [modelo y seed](docs/data-model.md)
y [progreso](docs/progress.md).

## Ejecutar

Desde la raíz del proyecto, con Docker Desktop funcionando:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up --build -d
docker compose ps
```

Cada API ejecuta migración y seed antes de aceptar solicitudes. Si falla, aborta
el arranque; no queda una API preparada sobre un esquema parcial. Reiniciar no
repone stock ni duplica datos. Los volúmenes existentes se conservan.

| Servicio | URL en el host |
|---|---|
| Central | http://127.0.0.1:8000 |
| Sucursal 1 | http://127.0.0.1:8001 |
| Sucursal 2 | http://127.0.0.1:8002 |
| Sucursal 3 | http://127.0.0.1:8003 |
| Panel | http://127.0.0.1:8501 |

Las API ofrecen `/health/live` y `/health/ready`; readiness comprueba acceso a
la base mediante SELECT 1 una vez terminado el arranque. Los contenedores se
comunican por sus nombres de servicio y rutas Linux. Los puertos se limitan a
127.0.0.1 y PostgreSQL no publica un puerto al host.

```powershell
docker compose logs --tail 50 central branch-1
docker compose down
```

`down` detiene los servicios conservando los volúmenes. Para volver a encender
las imágenes construidas, use `docker compose up -d`.

## Desarrollo y comprobaciones

Python 3.12 y la `.venv` existente, gestionada por uv:

```powershell
uv sync --locked
uv run ruff format --check src tests migrations
uv run ruff check src tests migrations
uv run mypy
uv run pytest -q
```

Sin `TEST_POSTGRES_URL`, pytest local informa las pruebas PostgreSQL como
omitidas. Para ejecutar la suite completa contra **PostgreSQL 17 real** y SQLite,
use el entorno temporal independiente, sin puertos publicados ni volúmenes del
proyecto:

```powershell
docker compose -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -f compose.test.yaml down
```

La segunda orden limpia los contenedores de prueba. PostgreSQL de pruebas usa
tmpfs y credenciales exclusivamente de prueba; no toca la base de desarrollo.
Se cubren restricciones, segunda ejecución, precio histórico, stock consumido,
rollback, carreras de arranque y snapshots duplicados/antiguos.

## Inicialización explícita

El arranque ya la ejecuta. Para comprobar idempotencia manualmente sin arrancar
otra API, en los servicios activos:

```powershell
docker compose exec branch-1 python -m retail_intelligence.cli init branch
docker compose exec central python -m retail_intelligence.cli init central
```

Fuera de Compose, los mismos comandos requieren variables de proceso:
`BRANCH_ID` (1, 2 o 3) y `DATABASE_URL` SQLite de archivo para sucursal; URL
`postgresql+psycopg` para central. No comparta un archivo entre sucursales. El CLI
no carga `.env` automáticamente; Compose lo usa para interpolar sus variables.

El seed central no carga inventario. El servicio interno que aplica snapshots
está implementado y probado, pero la sincronización por HTTP corresponde a la
siguiente etapa. El futuro publicador se ejecutará en el único proceso de cada
sucursal; no se deben aumentar workers ni réplicas sin coordinación adicional.
