# Distributed Retail Intelligence

Tienda simulada de tres sucursales y diez productos. Cada sucursal es autoridad
sobre su stock; el central conserva proyecciones eventualmente consistentes. El
MVP excluye pagos, devoluciones y transferencias.

Implementado: API de estado, panel, persistencia local/central, migraciones
separadas Alembic y seed idempotente. Cada sucursal tiene 100 unidades iniciales
por producto y un snapshot inicial en outbox. La API de sucursal registra ventas locales
transaccionales y las sincroniza por HTTP con el central. Los pronósticos siguen pendientes.

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

El seed central no carga inventario: recibe el snapshot inicial por HTTP desde
cada sucursal. El publicador se ejecuta en el único proceso de cada sucursal;
no aumente workers ni réplicas sin coordinación adicional.

## Ventas locales

La sucursal expone `GET /products`, `GET /inventory`, `GET /sales` y
`GET /sales/{sale_id}`. Para vender use `POST /sales`, con la cabecera
`Idempotency-Key` obligatoria (1 a 200 caracteres) y JSON, por ejemplo:

```json
{"lines": [{"product_id": 1, "quantity": 2}, {"product_id": 2, "quantity": 1}]}
```

Cantidades y producto deben ser enteros positivos y no se admiten productos
repetidos. El servidor calcula el total en centavos con el precio vigente y
conserva el precio histórico. Repetir la misma clave con las mismas líneas, en
cualquier orden, devuelve la venta original; contenido diferente produce 409.
Producto inexistente devuelve 404, stock insuficiente 409 y entrada inválida 422.
Venta, líneas, descuento, clave y evento se confirman juntos; un fallo revierte
todos los cambios. Los eventos se guardan en orden después del snapshot inicial.
Las escrituras SQLite se serializan mediante `BEGIN IMMEDIATE` con espera de
cinco segundos y hasta tres intentos completos ante bloqueos; si persiste el
bloqueo se responde 503. La sincronización ocurre de forma asíncrona.

## Sincronización y demo local

Compose configura `CENTRAL_URL=http://central:8000` y habilita explícitamente
`DEMO_SYNC_ENABLED=true` para las sucursales y el panel. Sin `CENTRAL_URL` no
arranca el publicador; el control demo está deshabilitado por defecto fuera de
Compose. Use únicamente un worker y una instancia por base SQLite.

El panel muestra pendientes, errores, última secuencia confirmada y última
actividad recibida por el central. El central considera desactualizada una
sucursal sin eventos recibidos durante cinco minutos; esto no distingue falta
de actividad de desconexión. Para actualizar manualmente, recargue el panel.
Pausar el envío no detiene la API ni SQLite; una solicitud ya en curso puede
terminar. Reanudar no desbloquea automáticamente un evento inválido. Revise el
error y corrija su causa antes de usar el botón de reintento, que vuelve a probar
el mismo evento sin descartarlo.

Endpoints de sucursal: `GET /sync/status` y, solo con la demo habilitada,
`POST /demo/sync/pause`, `/demo/sync/resume` y `/demo/sync/retry`. Central:
`GET /sync/status` y `POST /sync/events`. El lote tiene `events` (1 a 100); cada
evento lleva `event_id`, `schema_version`, `branch_id`, `sequence`,
`occurred_at`, `event_type` y `payload`. La respuesta contiene un resultado por
evento: `accepted` o `duplicate` confirman ese evento; `gap` y `temporary` son
reintentables; `invalid` y `conflict` requieren intervención. Un lote puede
confirmar parcialmente y cada evento se confirma en su propia transacción.

El envío es al menos una vez, con timeout de 5 segundos y espera exponencial
con jitter acotado. Un acuse perdido provoca un duplicado seguro. Un hueco no
adelanta el cursor: si la secuencia faltante existe en la outbox local, se
vuelve a poner pendiente desde ella; si no existe, se bloquea para intervención.
Nunca se borran eventos pendientes. No se promete entrega exactly-once.


## Demo offline reproducible

Con Docker Desktop y los servicios actuales en ejecución, desde la raíz:

```powershell
docker compose up --build -d
uv run python -m retail_intelligence.cli demo-offline
```

La demo crea **cuatro ventas reales** identificadas por ejecución en las bases
existentes: dos en sucursal 2 y una en cada una de las sucursales 1 y 3. No
elimina datos; consume dos unidades del producto 1 en la sucursal 2 y una en cada
una de las otras. Requiere existencias suficientes, publicadores activos, demo
habilitada en sucursales y ausencia de otras escrituras durante las mediciones.
Espera la sincronización, pausa solo el publicador 2, reinicia ese contenedor
con su volumen, reanuda y reenvía los cuatro eventos. Muestra estado antes,
durante y después; termina con código distinto de cero si falla. Intenta
reanudar la sucursal si se interrumpe después de pausar.

El botón y la demo anterior **pausan el publicador**; no interrumpen la red. La
prueba e2e adicional usa un proxy HTTP local que cierra la conexión de la
sucursal 2 sin responder, mientras su API y SQLite siguen vivas. La prueba crea un proyecto
Compose de nombre único y elimina únicamente sus propios contenedores y
volúmenes. Requiere Docker, uv y las imágenes `postgres:17` y
`distributed-retail-intelligence-tests:0.1.0`; esta última se puede construir
con `docker compose -f compose.test.yaml build tests`. Para ejecutarla:

```powershell
$env:RUN_DEMO_E2E = "1"
uv run pytest -s -q tests/test_demo_e2e.py
Remove-Item Env:RUN_DEMO_E2E
```

La prueba ejecuta la demo dos veces sobre la misma base aislada, comprueba
cursores, conteos, importes y stock antes y después del reenvío, y verifica la
recuperación tras el fallo real del proxy. Los servicios de prueba no acceden a
los volúmenes del proyecto del usuario.
