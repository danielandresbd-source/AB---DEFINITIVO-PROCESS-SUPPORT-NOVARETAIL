from pathlib import Path

import pandas as pd


# 1. Rutas del proyecto
RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
RUTA_EVENT_LOG = RAIZ_PROYECTO / "data" / "event_log.csv"
RUTA_METEO = RAIZ_PROYECTO / "outputs" / "meteo_validos.csv"
CARPETA_SALIDA = RAIZ_PROYECTO / "outputs"

for ruta in (RUTA_EVENT_LOG, RUTA_METEO):
    if not ruta.exists():
        raise FileNotFoundError(f"No encuentro el archivo: {ruta}")


# 2. Limpia el event log igual que d1_operativa.py
log = pd.read_csv(RUTA_EVENT_LOG, parse_dates=["event_ts", "ingested_at"])

log = (
    log.sort_values("ingested_at")
    .drop_duplicates(subset="event_id", keep="first")
    .copy()
)

# Horas del almacén de Sevilla: vienen en UTC sin indicarlo.
sevilla = log["warehouse"].eq("Sevilla") & log["source"].eq("almacen")
log.loc[sevilla, "event_ts"] = (
    log.loc[sevilla, "event_ts"]
    .dt.tz_localize("UTC")
    .dt.tz_convert("Europe/Madrid")
    .dt.tz_localize(None)
)


# 3. Reconstruye una fila por pedido con su tiempo total de entrega
eventos = log.pivot_table(
    index="order_id",
    columns="activity",
    values="event_ts",
    aggfunc="min",
)

descriptivos = (
    log.sort_values(["order_id", "event_ts"])
    .groupby("order_id")[["warehouse"]]
    .first()
)

pedidos = descriptivos.join(eventos)
pedidos["lead_time_h"] = (
    pedidos["delivered"] - pedidos["created"]
).dt.total_seconds() / 3600
pedidos = pedidos.dropna(subset=["lead_time_h"]).copy()
pedidos["fecha"] = pedidos["created"].dt.normalize()


# 4. Une con la meteorología válida por almacén (= ciudad) y fecha.
#    Solo se usan datos agregados; no interviene ningún order_id en la unión
#    con la fuente externa.
meteo = pd.read_csv(RUTA_METEO, parse_dates=["fecha"])

union = pedidos.reset_index().merge(
    meteo[["ciudad", "fecha", "tmax_c"]],
    left_on=["warehouse", "fecha"],
    right_on=["ciudad", "fecha"],
    how="inner",
)

union["calor_extremo"] = union["tmax_c"] >= 35
union["en_48h"] = union["lead_time_h"] <= 48

print(f"Pedidos entregados en el event log: {len(pedidos):,}")
print(f"Pedidos con meteorología válida (tras la unión): {len(union):,}")


# 5. Compara días de calor extremo y resto de días
resumen = union.groupby("calor_extremo").agg(
    pedidos_entregados=("en_48h", "size"),
    entregas_48h_pct=("en_48h", lambda v: 100 * v.mean()),
    p90_tiempo_total_h=("lead_time_h", lambda v: v.quantile(0.90)),
)

print("\nCalor extremo (máxima >= 35 °C) frente al resto de días:")
print(resumen.round(1).to_string())

por_ciudad = union.groupby(["warehouse", "calor_extremo"]).agg(
    pedidos_entregados=("en_48h", "size"),
    entregas_48h_pct=("en_48h", lambda v: 100 * v.mean()),
)

print("\nPor ciudad:")
print(por_ciudad.round(1).to_string())


# 6. Guarda el resumen
CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)
resumen.round(2).to_csv(CARPETA_SALIDA / "meteo_event_log_resumen.csv")
print(f"\nGuardado en: {CARPETA_SALIDA / 'meteo_event_log_resumen.csv'}")
