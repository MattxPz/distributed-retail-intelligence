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
