from pathlib import Path

import pandas as pd
import yaml


# 1. Rutas del proyecto
RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
RUTA_PEDIDOS = RAIZ_PROYECTO / "data" / "orders_raw.csv"
RUTA_CONTRATO = RAIZ_PROYECTO / "data" / "contrato_pedidos.yml"
CARPETA_SALIDA = RAIZ_PROYECTO / "outputs"

for ruta in (RUTA_PEDIDOS, RUTA_CONTRATO):
    if not ruta.exists():
        raise FileNotFoundError(f"No encuentro el archivo: {ruta}")


# 2. Lee el contrato y el CSV original (sin modificarlo)
with open(RUTA_CONTRATO, "r", encoding="utf-8") as archivo:
    contrato = yaml.safe_load(archivo)

pedidos = pd.read_csv(RUTA_PEDIDOS)

for columna in ["created_ts", "promised_ts", "delivered_ts"]:
    pedidos[columna] = pd.to_datetime(pedidos[columna], errors="coerce")


# 3. Conserva solo la primera copia de cada order_id
#    (en producción, la otra copia iría al registro de incidencias).
deduplicados = pedidos.drop_duplicates(subset="order_id", keep="first").copy()
print(f"Filas originales: {len(pedidos):,}")
print(f"Filas tras conservar una copia por pedido: {len(deduplicados):,}")


# 4. Aplica las mismas reglas que ingesta_pedidos.py, menos la clave única
d = deduplicados
campos_obligatorios = [
    campo
    for campo, definicion in contrato["fields"].items()
    if not definicion.get("nullable", False)
]
minimo_ingresos = contrato["fields"]["revenue_eur"]["minimum"]
minimo_coste = contrato["fields"]["variable_cost_eur"]["minimum"]

reglas_ok = (
    d[campos_obligatorios].notna().all(axis=1)
    & d["store"].isin(contrato["fields"]["store"]["allowed_values"])
    & d["channel"].isin(contrato["fields"]["channel"]["allowed_values"])
    & d["status"].isin(contrato["fields"]["status"]["allowed_values"])
    & (d["promised_ts"] >= d["created_ts"])
    & d["revenue_eur"].ge(minimo_ingresos)
    & d["variable_cost_eur"].ge(minimo_coste)
    & d["variable_cost_eur"].le(d["revenue_eur"])
    & (d["delivered_ts"].isna() | (d["delivered_ts"] >= d["created_ts"]))
    & (
        ((d["status"] == "delivered") & d["delivered_ts"].notna())
        | ((d["status"] != "delivered") & d["delivered_ts"].isna())
    )
)

validado_deduplicado = d.loc[reglas_ok].copy()


# 5. Calcula los mismos indicadores que ingesta_pedidos.py
def calcular_kpis(df):
    entregados = df["status"].eq("delivered")
    en_plazo = entregados & df["delivered_ts"].le(df["promised_ts"])
    ingresos = df["revenue_eur"].sum(min_count=1)
    costes = df["variable_cost_eur"].sum(min_count=1)
    margen = ingresos - costes

    return pd.Series({
        "filas": len(df),
        "pedidos_unicos": df["order_id"].nunique(),
        "entregados": int(entregados.sum()),
        "entregas_en_plazo_pct": 100 * en_plazo.sum() / entregados.sum(),
        "cancelaciones_pct": 100 * df["status"].eq("cancelled").mean(),
        "ingresos_eur": ingresos,
        "costes_variables_eur": costes,
        "margen_bruto_eur": margen,
        "margen_bruto_pct": 100 * margen / ingresos,
    })


validos_clave_unica = pd.read_csv(
    CARPETA_SALIDA / "pedidos_validos.csv",
    parse_dates=["created_ts", "promised_ts", "delivered_ts"],
)

comparacion = pd.DataFrame({
    "Original sin validar": calcular_kpis(pedidos),
    "Solo filas validadas": calcular_kpis(validos_clave_unica),
    "Validado y deduplicado": calcular_kpis(validado_deduplicado),
})

print("\nComparación de indicadores:")
print(comparacion.round(2).to_string())


# 6. Guarda la comparación
CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)
comparacion.round(2).to_csv(CARPETA_SALIDA / "kpis_comparacion.csv")
print(f"\nGuardado en: {CARPETA_SALIDA / 'kpis_comparacion.csv'}")
