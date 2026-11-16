import os

import httpx
import streamlit as st

st.title("Estado de servicios")
services = {
    "Central": os.getenv("CENTRAL_URL", "http://localhost:8000"),
    "Sucursal 1": os.getenv("BRANCH_1_URL", "http://localhost:8001"),
    "Sucursal 2": os.getenv("BRANCH_2_URL", "http://localhost:8002"),
    "Sucursal 3": os.getenv("BRANCH_3_URL", "http://localhost:8003"),
}
for name, base_url in services.items():
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/health/ready", timeout=3.0)
        st.write(f"{name}: {'listo' if response.status_code == 200 else 'no listo'}")
    except httpx.HTTPError:
        st.write(f"{name}: no disponible")


st.subheader("SincronizaciÃ³n central")
try:
    response = httpx.get(f"{services['Central'].rstrip('/')}/sync/status", timeout=3.0)
    response.raise_for_status()
    for item in response.json():
        state = "desactualizada" if item["stale"] else "reciente"
        st.write(
            f"Sucursal {item['branch_id']}: secuencia {item['last_sequence']}, "
            f"Ãºltima confirmaciÃ³n {item['last_synced_at'] or 'sin sincronizar'} ({state})"
        )
except (httpx.HTTPError, ValueError, KeyError):
    st.warning("No se puede consultar el estado central")

st.subheader("EnvÃ­o por sucursal")
demo_enabled = os.getenv("DEMO_SYNC_ENABLED", "false").lower() == "true"
for number in range(1, 4):
    base = services[f"Sucursal {number}"].rstrip("/")
    try:
        response = httpx.get(f"{base}/sync/status", timeout=3.0)
        response.raise_for_status()
        item = response.json()
        st.write(
            f"Sucursal {number}: pendientes {item['pending']}, "
            f"Ãºltima secuencia confirmada {item['last_confirmed_sequence']}, "
            f"pausada {item['paused']}, bloqueada {item['blocked']}, "
            f"error {item['last_error'] or 'ninguno'}"
        )
        if demo_enabled:
            action = "resume" if item["paused"] else "pause"
            label = "Reanudar envÃ­o" if item["paused"] else "Pausar envÃ­o"
            if st.button(f"{label} â€” sucursal {number}", key=f"sync-{number}"):
                result = httpx.post(f"{base}/demo/sync/{action}", timeout=3.0)
                result.raise_for_status()
                st.rerun()
            if item["blocked"] and st.button(
                f"Reintentar evento — sucursal {number}", key=f"retry-{number}"
            ):
                result = httpx.post(f"{base}/demo/sync/retry", timeout=3.0)
                result.raise_for_status()
                st.rerun()
    except (httpx.HTTPError, ValueError, KeyError):
        st.warning(f"No se puede consultar o controlar la sucursal {number}")
