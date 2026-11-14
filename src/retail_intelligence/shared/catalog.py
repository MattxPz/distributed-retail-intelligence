"""Stable, version-one demo catalog. Prices are integer cents."""

BRANCHES = [{"id": i, "name": f"Sucursal {i}"} for i in range(1, 4)]
PRODUCTS = [
    {"id": i, "sku": f"SKU-{i:03d}", "name": name, "price_cents": price}
    for i, (name, price) in enumerate(
        [
            ("Arroz 1 kg", 150),
            ("Leche 1 L", 110),
            ("Pan", 200),
            ("Huevos 12 unidades", 300),
            ("Aceite 1 L", 250),
            ("Azúcar 1 kg", 120),
            ("Sal 1 kg", 80),
            ("Café 250 g", 400),
            ("Agua 1 L", 75),
            ("Pasta 500 g", 130),
        ],
        start=1,
    )
]
INITIAL_QUANTITY = 100
