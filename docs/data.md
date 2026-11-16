# Datos para pronósticos

Los datasets son archivos de análisis separados de las bases de ventas y de la
outbox. Generarlos no vende productos ni modifica inventarios. La historia
sintética **no representa transacciones reales**.

## Historia sintética

`forecast-generate` produce por defecto 180 días para cada una de las 30
combinaciones (3 sucursales × 10 productos): 5.400 filas. La versión
`synthetic-v1` emplea un nivel propio por producto y sucursal, un factor por día
de la semana y ruido gaussiano reproducible. La semilla es configurable y cada
serie tiene su propio generador. Se usa Python 3.12, como en el resto del proyecto.
La fecha final fija de la demo es **2026-11-15**, inclusive; con 180 días el rango
comienza el **2026-05-20**. La fecha puede cambiarse explícitamente.

```powershell
$dataset = uv run python -m retail_intelligence.cli forecast-generate --seed 42 --days 180 --end-date 2026-11-15
uv run python -m retail_intelligence.cli forecast-prepare --source synthetic --input $dataset
uv run python -m retail_intelligence.cli forecast-query --dataset $dataset --branch 2 --product 3
```

`forecast-prepare` valida identidad, estructura y checksum de un dataset
sintético y conserva el mismo identificador; no crea una segunda copia cuando
el destino ya contiene exactamente esos datos. `forecast-query` muestra CSV
de una combinación. Para exportar a un archivo:

```powershell
uv run python -m retail_intelligence.cli forecast-query --dataset $dataset --branch 2 --product 3 | Set-Content -Encoding utf8 serie.csv
```

Los archivos se guardan por defecto bajo `data/generated/datasets/<dataset_id>/`,
excluido de Git. `--output-dir` cambia el directorio; elija una ubicación que no
contenga datos ajenos. Si ya existe un archivo con el mismo identificador pero
contenido distinto, el comando falla sin sobrescribirlo.

## Esquema y metadatos

Cada directorio contiene `daily.csv` y `metadata.json`. La clave única de cada
fila es `(business_date, branch_id, product_id)`:

| Campo | Significado |
|---|---|
| `business_date` | Fecha comercial `YYYY-MM-DD`, zona `America/Guayaquil` |
| `branch_id`, `product_id` | Identificadores estables de sucursal y producto |
| `units` | Entero no negativo; vacío si no hay observaciones y la cobertura es desconocida |
| `complete` | `true` si se conoce la cobertura completa de ese día y sucursal |
| `coverage_reason` | Motivo de completitud o de incertidumbre |

Los metadatos incluyen versión de esquema, versión del generador, origen,
semilla, fechas inclusivas, zona horaria, política de cobertura, significado
de unidades, conteos de completitud, número de filas, SHA-256 del CSV e
identificador del dataset. El identificador es SHA-256 de los metadatos
canónicos, que incluyen el checksum de los datos: una misma configuración y
contenido produce el mismo identificador; otro contenido produce otro dataset.
No contiene fecha de ejecución para mantener reproducibilidad.

## Ventas operativas y cobertura

Para preparar una instantánea de ventas proyectadas en PostgreSQL central,
configure `DATABASE_URL` en el entorno con una URL `postgresql+psycopg` accesible
desde la terminal. No incluya credenciales en Git:

```powershell
uv run python -m retail_intelligence.cli forecast-prepare --source operational --start-date 2026-01-01 --end-date 2026-06-30
```

El resultado también es un dataset inmutable en `data/generated/datasets/`.
Se agregan unidades de las líneas de venta por sucursal, producto y fecha
comercial, calculada desde `occurred_at` UTC en `America/Guayaquil`. En cada
ejecución se consulta el estado central disponible; si los datos recibidos
cambian, el checksum y el identificador también cambian. Una repetición sin
cambios reutiliza el mismo directorio. El comando solo lee las ventas.

**El cursor de sincronización no acredita que un día esté completo**: podría
haber ventas locales todavía pendientes sin que el central lo sepa. El sistema
actual tampoco emite un cierre diario con prueba de cobertura. Por tanto, el
preparador operativo marca todas las filas `complete=false` y
`coverage_reason=unverified_sync_coverage`. Una cifra positiva es lo observado
hasta la extracción, un límite inferior; una celda vacía significa desconocido,
no cero. No se rellenan días vacíos con cero basándose en el cursor ni en la
hora de la última sincronización.

La función interna de agregación admite días con cobertura independientemente
verificada y solo entonces completa una ausencia con cero y marca la fila
completa. La CLI actual no ofrece una forma de declarar arbitrariamente esos
días: para usarla en producción se necesita una fuente confiable de cierres
diarios y verificación de integridad de las secuencias, todavía no implementada.

Las ventas observadas durante quiebres de stock están restringidas por la
disponibilidad y **no equivalen a demanda real**. No hay todavía indicadores
históricos de quiebre ni modelos de pronóstico implementados; estas filas no
deben interpretarse como una medición completa de demanda.
