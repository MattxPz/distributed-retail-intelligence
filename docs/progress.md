# Progreso

## Fundamentos

- Preparados: reglas de trabajo, exclusiones de Git y documentación inicial de alcance y arquitectura.
- Aplicación, dependencias, migraciones y pruebas: aún no creadas.
- Comprobaciones de esta etapa: verificar que existen los cinco archivos solicitados y que se conservan `.git` y `.venv`. No se han ejecutado pruebas de aplicación.
- Siguiente paso: enviar el prompt 1 para crear la aplicación.

## Base ejecutable

- Paquete instalable, dependencias bloqueadas con uv, configuración validada y API de estado para central y sucursales; panel mínimo y Compose con volúmenes separados.
- Python 3.12.13 en la .venv existente; uv lock y uv sync --locked completados. Importación del paquete, Ruff, mypy y pytest: correctos (3 pruebas). pytest emitió una advertencia de deprecación de Starlette TestClient.
- Compose construido y arrancado; PostgreSQL healthy, healthchecks live y ready de las cuatro API y healthcheck del panel respondieron 200.
- Pendientes: modelo de datos, migraciones, ventas, outbox, publicador, sincronización y pronósticos. No se ha probado lógica de negocio inexistente.

## Persistencia: trabajo parcial

- Conexiones SQLite: claves foráneas activadas y espera de bloqueo de 5000 ms. Una prueba en SQLite comprueba ambas opciones y el rechazo de una referencia inexistente.
- Ruff y mypy sin errores; pytest: 4 pruebas aprobadas, con una advertencia de deprecación de Starlette.
- Etapa pendiente: modelos, migraciones separadas, seed y snapshot, arranque ordenado, diagrama y pruebas con PostgreSQL real. No se ha completado la persistencia.

## Persistencia completada — 2026-11-14

Esta sección reemplaza el estado parcial de las entradas anteriores.

- Modelos locales y centrales separados; revisiones Alembic branch_0001 y central_0001 incluidas en el paquete. Claves, FK, índices, restricciones de cantidad/precio y unicidad por sucursal implementados.
- Seed estable de tres sucursales y diez productos. Cada sucursal inicia con 100 unidades de cada producto y un único snapshot versión 1 en outbox, secuencia 1. Repetir el seed conserva stock consumido, precios existentes y evento original.
- Inicialización transaccional antes de aceptar solicitudes: BEGIN IMMEDIATE en SQLite, advisory lock de transacción en PostgreSQL. Fallos revierten migración y seed. La base SQLite rechaza un BRANCH_ID distinto al original.
- Servicio central de snapshot: proyección, deduplicación y cursor atómicos; rechaza conflictos y snapshots que sobrescribirían estado posterior. El seed central no carga stock. El transporte HTTP queda para la etapa de sincronización.
- Documentación: README actualizado y docs/data-model.md con diagramas ER, catálogo, inventario inicial, claves, bloqueo y contrato de secuencias.

Comprobaciones ejecutadas:

- uv sync --locked completado en la .venv existente; importación correcta. Ruff lint/formato y mypy sin errores (19 archivos de código comprobados por mypy).
- Pytest local en Windows: 19 aprobadas y 17 omitidas por no definir TEST_POSTGRES_URL. No se contabilizan las omitidas como aprobadas.
- Suite final en compose.test.yaml: 36 aprobadas, ninguna omitida, contra SQLite real y PostgreSQL 17.12 real. Incluye bases nuevas, segunda ejecución, restricciones, precio histórico, rollback de esquema/seed, dos procesos concurrentes, aplicación desde outbox y snapshots duplicados, concurrentes y antiguos.
- Advertencia existente: Starlette TestClient depreca su integración con HTTPX. No impidió las pruebas.
- Compose del proyecto reconstruido y arrancado. Revisiones verificadas en las cuatro bases; tres sucursales y diez productos en cada catálogo; 1.000 unidades y un evento pendiente por sucursal; central sin stock proyectado y con tres cursores en cero.
- CLI init repetido en los cuatro servicios y posterior reinicio de las cuatro API: inventario y payloads de snapshots exactamente iguales antes y después. Ocho healthchecks de API y el del panel respondieron HTTP 200.
- Entorno temporal de pruebas detenido y eliminado mediante compose.test.yaml down. Los seis servicios del proyecto permanecen activos y sus volúmenes se conservaron.
- git diff --check sin errores; sin staging, commit ni push.

Siguiente etapa: API de ventas transaccionales e idempotencia operativa; posteriormente publicador/receptor HTTP con secuencias y recuperación. Las tablas y contratos ya están preparados, pero esas funcionalidades no se simulan en esta etapa.


## Ventas locales

- POST /sales y consultas locales de productos, inventario y ventas. Validación estricta, precio histórico en centavos, fecha comercial de Guayaquil e instante UTC.
- BEGIN IMMEDIATE, descuentos condicionales y transacción única para venta, líneas, idempotencia, secuencia y outbox; reintentos acotados de la transacción completa ante bloqueo.
- Comprobaciones: Ruff y mypy correctos; 15 pruebas de ventas locales aprobadas. Suite completa anterior con el mismo código de aplicación: 49 pruebas aprobadas contra SQLite y PostgreSQL 17, antes de añadir dos pruebas locales adicionales; advertencia conocida de Starlette TestClient.
- La construcción de Docker falló al consultar Docker Hub por DNS; se ejecutó la suite usando la imagen de pruebas existente y el código actual montado en solo lectura. El entorno de pruebas se detuvo. Las imágenes de aplicación existentes no se reconstruyeron; el transporte de outbox sigue pendiente.


## Sincronización: implementación en verificación

- Se prepararon receptor HTTP por lotes con resultado individual, proyección transaccional y deduplicación en PostgreSQL; publicador secuencial con timeout, reintentos, estado y controles demo; migraciones separadas para estado de sincronización.
- Ruff y mypy correctos; pytest local: 36 aprobadas y 18 omitidas por falta de conexión a PostgreSQL. Los ensayos de publicador con transporte simulado pasan.
- PostgreSQL real y el flujo HTTP completo permanecen sin verificar: Docker Desktop está iniciado pero el sandbox deniega acceso a su canal local. No se han reconstruido ni arrancado servicios con estos cambios. Esta etapa no está terminada.


## Sincronización completada — 2026-11-15

- Publicador HTTP por sucursal, timeout de cinco segundos, reintentos con backoff/jitter, confirmación por evento y outbox durable. Huecos reponen desde la secuencia faltante si sigue disponible; errores permanentes bloquean y requieren intervención.
- Receptor por lotes en PostgreSQL: validación del sobre y payload, serialización por cursor de sucursal, deduplicación mediante restricciones únicas, y aplicación transaccional de snapshots, ventas, stock y cursor. Última recepción y estado desactualizado expuestos por central.
- Migraciones branch_0002 y central_0002; demo explícita para pausar/reanudar y reintentar, con estado visible en el panel. Una solicitud en vuelo puede completarse al pausar.
- Ruff y mypy correctos; suite completa en imagen de pruebas existente y código montado en solo lectura: 58 aprobadas, PostgreSQL 17 real y SQLite real. Advertencias de deprecación de Starlette TestClient y adaptador datetime SQLite de pruebas.
- Prueba HTTP de extremo a extremo con contenedores y bases temporales aisladas: snapshot, pausa, venta local, reanudación, stock central 98 y una venta. Pruebas adicionales cubren duplicados concurrentes, rollback anterior al commit, respuesta perdida tras commit, huecos, contenido incompatible y reenvío tras reabrir base.
- Contenedores temporales de pruebas detenidos y eliminados; no se reconstruyeron ni arrancaron los servicios del proyecto. Para ejecutar el código nuevo use docker compose up --build -d.
