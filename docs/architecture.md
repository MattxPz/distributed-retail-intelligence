# Arquitectura prevista

Esta es una propuesta para etapas posteriores, no una descripciÃ³n de componentes ya implementados. El MVP simularÃ¡ tres sucursales y diez productos, con inventario inicial, ventas, sincronizaciÃ³n y pronÃ³stico. Quedan fuera pagos, devoluciones y transferencias.

## Componentes y propiedad de datos

- Un monorepositorio Python 3.12: FastAPI para las API, SQLAlchemy 2 y Alembic para persistencia y migraciones. Cada sucursal tendrÃ¡ su propia base SQLite; PostgreSQL 17 almacenarÃ¡ el estado central.
- Cada sucursal es autoridad exclusiva de su stock y valida ventas localmente. El central conserva proyecciones eventualmente consistentes y no autoriza ventas locales.
- HTTPX enviarÃ¡ eventos; Streamlit y Plotly presentarÃ¡n el panel. pandas y scikit-learn servirÃ¡n al anÃ¡lisis y pronÃ³stico. `uv` gestionarÃ¡ el entorno; pytest, Ruff y mypy verificarÃ¡n el proyecto. Las versiones compatibles se resolverÃ¡n y bloquearÃ¡n al crear el entorno.
- Docker Compose ejecutarÃ¡ tres instancias de sucursal, central, PostgreSQL y panel. Desconectar una sucursal significarÃ¡ bloquear Ãºnicamente la comunicaciÃ³n de sincronizaciÃ³n: su API y su base local seguirÃ¡n activas.

```mermaid
flowchart LR
  B1[Sucursal 1: API + SQLite] -->|outbox HTTP| C[API central]
  B2[Sucursal 2: API + SQLite] -->|outbox HTTP| C
  B3[Sucursal 3: API + SQLite] -->|outbox HTTP| C
  C --> P[(PostgreSQL 17)]
  D[Panel Streamlit] --> C
  F[PronÃ³stico] --> C
```

## Transacciones y contrato de eventos

Una venta vÃ¡lida, su descuento de inventario y la inserciÃ³n del evento en la outbox se confirmarÃ¡n en una sola transacciÃ³n local. Una operaciÃ³n fallida no dejarÃ¡ efectos parciales; se impedirÃ¡ vender mÃ¡s unidades que las disponibles. El dinero se representarÃ¡ en centavos enteros y las cantidades vendidas en unidades enteras positivas. Las fechas de eventos serÃ¡n UTC; la fecha comercial se determinarÃ¡ en `America/Guayaquil`.

Cada evento tendrÃ¡ al menos identificador Ãºnico, versiÃ³n de esquema, identificador de sucursal, secuencia monotÃ³nica por sucursal, tipo, fecha UTC y carga Ãºtil. Los movimientos identificarÃ¡n el producto, la venta y el cambio de unidades. La secuencia se asignarÃ¡ de forma transaccional con la outbox. El envÃ­o HTTP serÃ¡ **al menos una vez** y podrÃ¡ repetir eventos; no se promete entrega exactly-once. El central registrarÃ¡ persistentemente cada identificador y aplicarÃ¡ sus efectos en una misma transacciÃ³n. Una repeticiÃ³n idÃ©ntica se reconocerÃ¡ sin aplicar de nuevo; una colisiÃ³n de identificador con contenido distinto se rechazarÃ¡ y seÃ±alarÃ¡.

El inventario central comenzarÃ¡ con un snapshot inicial versionado por sucursal, que incluye su secuencia de corte y existencias por producto. Solo se aplicarÃ¡n movimientos posteriores a ese corte, en orden de secuencia por sucursal. El central persistirÃ¡ el Ãºltimo nÃºmero aplicado; ante un hueco no avanzarÃ¡ ni aplicarÃ¡ eventos posteriores de esa sucursal hasta recuperar los faltantes. Un snapshot antiguo o igual no sobrescribirÃ¡ stock mÃ¡s reciente; uno nuevo solo se aceptarÃ¡ mediante un procedimiento explÃ­cito de reconciliaciÃ³n que verifique su versiÃ³n y corte, y preserve o reaplique los movimientos posteriores. El protocolo concreto y la recuperaciÃ³n de huecos se implementarÃ¡n y probarÃ¡n en etapas posteriores.

## Errores y recuperaciÃ³n

Errores de validaciÃ³n o stock insuficiente rechazarÃ¡n la venta sin efectos parciales. Fallos de red o del central conservarÃ¡n la outbox pendiente para reintento; un acuse solo marcarÃ¡ enviado el evento confirmado por el central. Errores transitorios serÃ¡n reintentables; esquemas invÃ¡lidos, conflictos de identidad y huecos se registrarÃ¡n y expondrÃ¡n para diagnÃ³stico sin descartar datos silenciosamente. La implementaciÃ³n definirÃ¡ lÃ­mites y polÃ­tica de reintentos.

## PronÃ³stico

Se compararÃ¡ un promedio histÃ³rico con Ridge usando separaciÃ³n temporal de entrenamiento, validaciÃ³n y prueba. Las caracterÃ­sticas y transformaciones se ajustarÃ¡n solo con informaciÃ³n disponible hasta cada corte, sin datos futuros. Las mÃ©tricas y resultados se definirÃ¡n y medirÃ¡n al implementar, sin anticiparlos.

## Estructura futura

```text
src/retail_intelligence/
  branch/
  central/
  forecasting/
  dashboard/
  shared/
  cli.py
migrations/{branch,central}/
tests/{unit,integration,e2e}/
docs/
.github/workflows/
compose.yaml
Dockerfile
pyproject.toml
uv.lock
.env.example
README.md
```

`shared` contendrÃ¡ contratos y configuraciÃ³n comunes, sin mezclar lÃ³gica de negocio. Dentro de cada mÃ³dulo se separarÃ¡n rutas, servicios y persistencia cuando tengan responsabilidades reales.

## Fases y criterios de aceptaciÃ³n

1. Fundamentos: reglas, exclusiones y documentaciÃ³n inicial; no se requiere aplicaciÃ³n ejecutable.
2. AplicaciÃ³n y persistencia: tres sucursales independientes; ventas, descuento de stock y outbox atÃ³micos; pruebas de invariantes y fallos.
3. SincronizaciÃ³n: snapshot inicial versionado, orden y recuperaciÃ³n de huecos; reintentos y deduplicaciÃ³n persistente y atÃ³mica, comprobados ante duplicados y desconexiones.
4. PronÃ³stico y panel: comparaciÃ³n temporal sin fuga de informaciÃ³n y visualizaciÃ³n de datos disponibles.
5. IntegraciÃ³n: Compose y comprobaciones automatizadas de los flujos y fallos relevantes. El orden y alcance detallado de las etapas posteriores se concretarÃ¡n en sus instrucciones.

## Base ejecutable actual

Las API exponen vida del proceso y readiness mediante una consulta SELECT 1 a la base configurada; esta comprobaciÃ³n no certifica migraciones ni datos de negocio. Compose asigna un volumen SQLite independiente por sucursal y uno a PostgreSQL. Las URLs entre contenedores usan nombres de servicio; los puertos del host se limitan a 127.0.0.1. Cada sucursal ejecuta un Ãºnico worker. El publicador aÃºn no existe: al implementarlo se ejecutarÃ¡ en ese proceso Ãºnico por sucursal, sin rÃ©plicas ni workers adicionales hasta disponer de coordinaciÃ³n segura. Ventas, outbox, migraciones, sincronizaciÃ³n y pronÃ³sticos siguen pendientes.
