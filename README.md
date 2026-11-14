# Distributed Retail Intelligence

Base para una tienda simulada de tres sucursales y diez productos, con futuras ventas transaccionales, inventario, sincronización y pronósticos. Cada sucursal será autoridad sobre su stock; el central mantendrá proyecciones eventualmente consistentes. El MVP no incluye pagos, devoluciones ni transferencias. Consulte [arquitectura](docs/architecture.md) y [progreso](docs/progress.md).

Actualmente existen las API de estado, configuración y panel. Aún no hay ventas, inventario, sincronización ni pronósticos implementados.

## Inicio con Compose

Requiere Docker con Compose. Cree `.env` a partir de `.env.example` si aún no existe y ajuste las credenciales locales; no sobrescriba un `.env` existente. Después ejecute:

```powershell
docker compose up --build -d
docker compose ps
```

El panel está en http://127.0.0.1:8501. Las API central y de las sucursales usan respectivamente los puertos 8000, 8001, 8002 y 8003 en localhost. Cada API ofrece `/health/live` para vida del proceso y `/health/ready` para conectividad con su base. Readiness comprueba `SELECT 1`, no migraciones ni datos de negocio. PostgreSQL no se publica en el host. Dentro de Compose se usan los nombres de servicio, no localhost, para la comunicación entre contenedores.

Para detener los servicios conservando los volúmenes:

```powershell
docker compose down
```

## Desarrollo

Con Python 3.12 y `uv`, desde la raíz:

```powershell
uv sync --locked
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest
```

Las sucursales requieren `BRANCH_ID` (1, 2 o 3) y `DATABASE_URL` SQLite de archivo. Cada instancia debe usar una ruta distinta y un único proceso worker. El central requiere `DATABASE_URL` PostgreSQL con el controlador `postgresql+psycopg`. Compose configura estas variables y los volúmenes independientes.

No existe aún publicador de eventos. Cuando se implemente, se ejecutará dentro del único proceso de cada sucursal; no se deben aumentar workers ni réplicas hasta implementar coordinación que garantice un solo publicador por sucursal.
