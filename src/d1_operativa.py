# Path localiza los archivos dentro del proyecto.
from pathlib import Path

# pandas permite leer y analizar tablas.
import pandas as pd


# La carpeta principal contiene las carpetas data/, src/ y outputs/.
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent

# Ruta al CSV del event log.
RUTA_EVENT_LOG = RAIZ_PROYECTO / "data" / "event_log.csv"


# Leemos el CSV y convertimos estas columnas a fecha y hora.
log = pd.read_csv(
    RUTA_EVENT_LOG,
    parse_dates=["event_ts", "ingested_at"],
)


# Cada pedido puede tener varios eventos.
print(f"Filas del event log: {len(log):,}")
print(f"Pedidos distintos: {log['order_id'].nunique():,}")
print(
    "Eventos con event_id repetido:",
    f"{log['event_id'].duplicated().sum():,}",
)

print("\nPrimeros cinco eventos:")
print(log.head().to_string(index=False))

print("\nEventos por actividad en el archivo original:")
print(log["activity"].value_counts().to_string())


# Ordenamos por la hora de ingesta para conservar la primera copia
# recibida cuando un event_id aparece más de una vez.
log_limpio = log.sort_values("ingested_at").copy()

# Quitamos copias repetidas del mismo evento.
log_limpio = log_limpio.drop_duplicates(
    subset="event_id",
    keep="first",
).copy()

# Calculamos cuántas filas repetidas quitamos.
duplicados_quitados = len(log) - len(log_limpio)


# En esta simulación, las horas de los eventos del sistema de almacén
# de Sevilla están registradas en UTC, aunque no lo indiquen.
eventos_sevilla = (
    log_limpio["warehouse"].eq("Sevilla")
    & log_limpio["source"].eq("almacen")
)

# Convertimos esas horas a la zona horaria de Madrid.
log_limpio.loc[eventos_sevilla, "event_ts"] = (
    log_limpio.loc[eventos_sevilla, "event_ts"]
    .dt.tz_localize("UTC")
    .dt.tz_convert("Europe/Madrid")
    .dt.tz_localize(None)
)


# Ordenamos los eventos de cada pedido por fecha y hora.
log_limpio = (
    log_limpio
    .sort_values(["order_id", "event_ts"])
    .reset_index(drop=True)
)


# Mostramos los resultados de la limpieza.
print(f"\nFilas después de limpiar: {len(log_limpio):,}")
print(f"Duplicados quitados: {duplicados_quitados:,}")
print(
    "event_id repetidos después de limpiar:",
    log_limpio["event_id"].duplicated().sum(),
)
print(f"Eventos de Sevilla con hora corregida: {eventos_sevilla.sum():,}")


# Creamos una tabla con una fila por pedido y una columna por actividad.
eventos_por_pedido = log_limpio.pivot_table(
    index="order_id",
    columns="activity",
    values="event_ts",
    aggfunc="min",
)


# Añadimos almacén, transportista y canal a cada pedido.
datos_pedido = (
    log_limpio
    .sort_values(["order_id", "event_ts"])
    .groupby("order_id")[["warehouse", "carrier", "channel"]]
    .first()
)

# Unimos los datos descriptivos con las fechas de las actividades.
flujo = datos_pedido.join(eventos_por_pedido)


# Esta función calcula la duración en horas entre dos actividades.
def horas_entre(actividad_inicial, actividad_final):
    return (
        flujo[actividad_final] - flujo[actividad_inicial]
    ).dt.total_seconds() / 3600


# Tiempo desde el pago hasta que se recoge el pedido.
flujo["espera_picking_h"] = horas_entre(
    "payment_confirmed",
    "picked",
)

# Tiempo desde que se recoge hasta que se empaqueta.
flujo["packing_h"] = horas_entre(
    "picked",
    "packed",
)

# Tiempo desde que se empaqueta hasta que lo recoge el transportista.
flujo["espera_recogida_h"] = horas_entre(
    "packed",
    "shipped",
)

# Tiempo desde el envío hasta la entrega.
flujo["transporte_h"] = horas_entre(
    "shipped",
    "delivered",
)

# Tiempo total desde la creación hasta la entrega.
flujo["lead_time_h"] = horas_entre(
    "created",
    "delivered",
)


# Calculamos P50 y P90 para cada etapa.
# P50: la mitad de los pedidos completó la etapa en ese tiempo o menos.
# P90: el 90 % de los pedidos completó la etapa en ese tiempo o menos.
etapas = [
    "espera_picking_h",
    "packing_h",
    "espera_recogida_h",
    "transporte_h",
]

resumen_etapas = pd.DataFrame(
    {
        "P50_h": flujo[etapas].quantile(0.50),
        "P90_h": flujo[etapas].quantile(0.90),
    }
)

print(f"\nPedidos reconstruidos: {len(flujo):,}")
print("\nP50 y P90 de duración por etapa:")
print(resumen_etapas.round(2).to_string())


# Fijamos la hora a la que simulamos revisar los pedidos abiertos.
CORTE = pd.Timestamp("2026-08-21 12:00:00")

# Asociamos cada actividad abierta con el P90 de la etapa siguiente.
p90_etapas = resumen_etapas["P90_h"]

p90_siguiente = {
    "payment_confirmed": p90_etapas["espera_picking_h"],
    "picked": p90_etapas["packing_h"],
    "packed": p90_etapas["espera_recogida_h"],
    "shipped": p90_etapas["transporte_h"],
}


# Solo usamos eventos que el sistema había recibido antes del corte.
eventos_conocidos = log_limpio.loc[
    log_limpio["ingested_at"] <= CORTE
].copy()

# Reconstruimos el último estado conocido de cada pedido.
ultimo_estado = (
    eventos_conocidos
    .sort_values(["order_id", "event_ts"])
    .groupby("order_id")
    .tail(1)
    .set_index("order_id")
)

# Estas actividades representan pedidos que siguen en curso.
estados_abiertos = list(p90_siguiente.keys())

ultimo_estado = ultimo_estado.loc[
    ultimo_estado["activity"].isin(estados_abiertos)
].copy()


# Calculamos cuánto tiempo llevaba cada pedido en su estado al corte.
ultimo_estado["horas_en_estado"] = (
    CORTE - ultimo_estado["event_ts"]
).dt.total_seconds() / 3600

# Asignamos a cada pedido el P90 de la etapa que tiene por delante.
ultimo_estado["p90_etapa_siguiente_h"] = (
    ultimo_estado["activity"].map(p90_siguiente)
)

# La regla inicial señala pedidos que ya superaron ese P90.
ultimo_estado["requiere_revision"] = (
    ultimo_estado["horas_en_estado"]
    > ultimo_estado["p90_etapa_siguiente_h"]
)


# WIP significa pedidos que siguen en curso en el momento del corte.
# Como ultimo_estado tiene una fila por pedido, el desglose por actividad
# debe sumar exactamente el total de pedidos abiertos.
wip_por_actividad = (
    ultimo_estado["activity"]
    .value_counts(dropna=False)
    .sort_index()
)

print(f"\nPedidos abiertos conocidos a las {CORTE}: {len(ultimo_estado):,}")
print("\nWIP por última actividad:")
print(wip_por_actividad.to_string())
print(f"Suma del desglose WIP: {wip_por_actividad.sum():,}")
print(f"Pedidos abiertos distintos: {ultimo_estado.index.nunique():,}")

# Esta comprobación confirma que cada pedido abierto se cuenta una sola vez.
assert wip_por_actividad.sum() == ultimo_estado.index.nunique()

# WIP por actividad y transportista (tabla del informe).
wip_por_transportista = pd.crosstab(
    ultimo_estado["activity"],
    ultimo_estado["carrier"],
    margins=True,
    margins_name="Total",
)

print("\nWIP por actividad y transportista:")
print(wip_por_transportista.to_string())


# Separamos los pedidos señalados por la regla inicial.
pedidos_a_revisar = ultimo_estado.loc[
    ultimo_estado["requiere_revision"]
].copy()

print(f"\nPedidos señalados para revisión: {len(pedidos_a_revisar):,}")
print("\nPedidos señalados por transportista:")
print(pedidos_a_revisar["carrier"].value_counts().to_string())

print("\nPrimeros pedidos señalados:")
print(
    pedidos_a_revisar[
        [
            "activity",
            "carrier",
            "warehouse",
            "horas_en_estado",
            "p90_etapa_siguiente_h",
        ]
    ]
    .head(10)
    .to_string()
)


# Comparamos los pedidos abiertos con su resultado final conocido.
evaluacion = ultimo_estado.join(
    flujo[["lead_time_h"]],
    how="left",
)

# Solo evaluamos pedidos con un tiempo de entrega final conocido.
evaluacion = evaluacion.dropna(
    subset=["lead_time_h"]
).copy()

# La promesa del ejemplo es entregar dentro de las 48 horas desde la creación.
evaluacion["llego_tarde"] = evaluacion["lead_time_h"] > 48


# Contamos aciertos y falsas alarmas de la regla inicial.
verdaderos_positivos = int(
    (
        evaluacion["requiere_revision"]
        & evaluacion["llego_tarde"]
    ).sum()
)

falsos_positivos = int(
    (
        evaluacion["requiere_revision"]
        & ~evaluacion["llego_tarde"]
    ).sum()
)

falsos_negativos = int(
    (
        ~evaluacion["requiere_revision"]
        & evaluacion["llego_tarde"]
    ).sum()
)

total_alertas = verdaderos_positivos + falsos_positivos
total_retrasos = verdaderos_positivos + falsos_negativos

precision = (
    verdaderos_positivos / total_alertas
    if total_alertas > 0
    else 0
)

cobertura = (
    verdaderos_positivos / total_retrasos
    if total_retrasos > 0
    else 0
)

print(f"\nPedidos abiertos evaluables: {len(evaluacion):,}")
print(f"Alertas acertadas: {verdaderos_positivos:,}")
print(f"Falsas alarmas: {falsos_positivos:,}")
print(f"Retrasos no señalados: {falsos_negativos:,}")
print(f"Precisión: {precision:.1%}")
print(f"Cobertura de retrasos: {cobertura:.1%}")


# Asignamos una franja horaria según la hora de entrada en una actividad.
def asignar_franja(fechas):
    return pd.cut(
        fechas.dt.hour,
        bins=[-1, 5, 11, 17, 23],
        labels=["madrugada", "mañana", "tarde", "noche"],
    )


# Buscamos entregas que el sistema recibió antes de la hora de corte.
entregas_recibidas = (
    log_limpio.loc[
        log_limpio["activity"].eq("delivered"),
        ["order_id", "ingested_at"],
    ]
    .drop_duplicates(subset="order_id")
)

ids_entregados_antes_del_corte = entregas_recibidas.loc[
    entregas_recibidas["ingested_at"] <= CORTE,
    "order_id",
]

# Usamos pedidos entregados antes del corte como historial para estimar
# cuánto tiempo suele quedar desde cada etapa hasta la entrega.
historial = flujo.loc[ids_entregados_antes_del_corte].copy()
historial = historial.dropna(subset=["delivered"])

partes_historial = []

for estado in p90_siguiente:
    parte = historial.loc[
        historial[estado].notna(),
        ["carrier", "delivered", estado],
    ].copy()

    parte["horas_restantes"] = (
        parte["delivered"] - parte[estado]
    ).dt.total_seconds() / 3600

    # Quitamos duraciones negativas por registros incoherentes.
    parte = parte.loc[parte["horas_restantes"] >= 0].copy()

    parte["estado"] = estado
    parte["franja"] = asignar_franja(parte[estado])

    partes_historial.append(
        parte[
            [
                "estado",
                "carrier",
                "franja",
                "horas_restantes",
            ]
        ]
    )

historial_largo = pd.concat(
    partes_historial,
    ignore_index=True,
)


# Calculamos el Q75 del tiempo restante por estado, transportista y franja.
q75_restante = (
    historial_largo
    .groupby(
        ["estado", "carrier", "franja"],
        observed=True,
    )["horas_restantes"]
    .agg(
        q75=lambda valores: valores.quantile(0.75),
        pedidos="size",
    )
    .reset_index()
)

print(
    "\nEntregas recibidas antes del corte:",
    f"{historial.index.nunique():,}",
)
print("\nQ75 estimado por estado, transportista y franja:")
print(q75_restante.head(12).to_string(index=False))


# Preparamos referencias Q75 desde la más específica a la más general.
q75_exacto = q75_restante.set_index(
    ["estado", "carrier", "franja"]
)["q75"]

q75_transportista = (
    historial_largo
    .groupby(["estado", "carrier"])["horas_restantes"]
    .quantile(0.75)
)

q75_estado = (
    historial_largo
    .groupby("estado")["horas_restantes"]
    .quantile(0.75)
)


# Busca el Q75 apropiado para cada pedido.
# Si falta el dato más específico, utiliza una referencia más general.
def buscar_q75(fila):
    clave_exacta = (
        fila["activity"],
        fila["carrier"],
        fila["franja"],
    )

    if clave_exacta in q75_exacto.index:
        return pd.Series(
            {
                "q75_restante_h": q75_exacto.loc[clave_exacta],
                "nivel_referencia": (
                    "estado + transportista + franja"
                ),
            }
        )

    clave_transportista = (
        fila["activity"],
        fila["carrier"],
    )

    if clave_transportista in q75_transportista.index:
        return pd.Series(
            {
                "q75_restante_h": q75_transportista.loc[
                    clave_transportista
                ],
                "nivel_referencia": "estado + transportista",
            }
        )

    return pd.Series(
        {
            "q75_restante_h": q75_estado.loc[fila["activity"]],
            "nivel_referencia": "solo estado",
        }
    )


# Copiamos los pedidos abiertos identificados a la hora de corte.
predicciones_q75 = ultimo_estado.copy()

# Añadimos la hora de creación y el tiempo total de entrega.
predicciones_q75 = predicciones_q75.join(
    flujo[["created", "lead_time_h"]],
    how="left",
)

# El event log no incluye fecha prometida. Se SUPONE una promesa de
# 48 horas desde la creación (misma definición que "llego_tarde").
predicciones_q75["promised_ts"] = (
    predicciones_q75["created"]
    + pd.Timedelta(hours=48)
)

# Asignamos una franja según la hora de entrada en el estado actual.
predicciones_q75["franja"] = asignar_franja(
    predicciones_q75["event_ts"]
)

# Estimamos el tiempo restante con el Q75 adecuado.
referencias = predicciones_q75.apply(
    buscar_q75,
    axis=1,
)

predicciones_q75[
    ["q75_restante_h", "nivel_referencia"]
] = referencias

# Calculamos la fecha estimada de llegada.
predicciones_q75["llegada_estimada_ts"] = (
    predicciones_q75["event_ts"]
    + pd.to_timedelta(
        predicciones_q75["q75_restante_h"],
        unit="h",
    )
)

# Señalamos si la llegada estimada superaría la fecha prometida.
predicciones_q75["alerta_q75"] = (
    predicciones_q75["llegada_estimada_ts"]
    > predicciones_q75["promised_ts"]
)


# Evaluamos la regla Q75 solo en pedidos cuyo resultado final conocemos.
predicciones_evaluables = predicciones_q75.dropna(
    subset=["lead_time_h"]
).copy()

predicciones_evaluables["llego_tarde"] = (
    predicciones_evaluables["lead_time_h"] > 48
)


# Contamos aciertos, falsas alarmas y retrasos no señalados de Q75.
aciertos_q75 = int(
    (
        predicciones_evaluables["alerta_q75"]
        & predicciones_evaluables["llego_tarde"]
    ).sum()
)

falsas_alarmas_q75 = int(
    (
        predicciones_evaluables["alerta_q75"]
        & ~predicciones_evaluables["llego_tarde"]
    ).sum()
)

retrasos_no_señalados_q75 = int(
    (
        ~predicciones_evaluables["alerta_q75"]
        & predicciones_evaluables["llego_tarde"]
    ).sum()
)

total_alertas_q75 = aciertos_q75 + falsas_alarmas_q75
total_retrasos_q75 = (
    aciertos_q75 + retrasos_no_señalados_q75
)

precision_q75 = (
    aciertos_q75 / total_alertas_q75
    if total_alertas_q75 > 0
    else 0
)

cobertura_q75 = (
    aciertos_q75 / total_retrasos_q75
    if total_retrasos_q75 > 0
    else 0
)

# Calculamos la proporción de retrasos observada en el grupo evaluable.
prevalencia_retrasos = (
    predicciones_evaluables["llego_tarde"].mean()
    if len(predicciones_evaluables) > 0
    else 0
)

# Comparamos la precisión Q75 con la proporción base de retrasos.
veces_sobre_prevalencia = (
    precision_q75 / prevalencia_retrasos
    if prevalencia_retrasos > 0
    else 0
)

print("\nEvaluación de la regla Q75:")
print(
    "Pedidos abiertos evaluables:",
    f"{len(predicciones_evaluables):,}",
)
print(f"Pedidos señalados: {total_alertas_q75:,}")
print(f"Alertas acertadas: {aciertos_q75:,}")
print(f"Falsas alarmas: {falsas_alarmas_q75:,}")
print(
    "Retrasos no señalados:",
    f"{retrasos_no_señalados_q75:,}",
)
print(f"Precisión Q75: {precision_q75:.1%}")
print(f"Cobertura de retrasos Q75: {cobertura_q75:.1%}")
print(f"Proporción base de retrasos: {prevalencia_retrasos:.1%}")
print(
    "Precisión frente a proporción base:",
    f"{veces_sobre_prevalencia:.1f} veces",
)

print("\nReferencia utilizada para estimar:")
print(
    predicciones_q75["nivel_referencia"]
    .value_counts()
    .to_string()
)


# Seleccionamos pedidos con tiempo total de entrega calculado.
entregados = flujo.dropna(
    subset=["lead_time_h"]
).copy()

# Calculamos qué porcentaje se entregó en 48 horas o menos.
tasa_a_tiempo = 100 * (
    entregados["lead_time_h"] <= 48
).mean()

# Calculamos el P90 del tiempo total de entrega.
p90_entrega_total = entregados["lead_time_h"].quantile(0.90)

print(f"\nPedidos entregados evaluables: {len(entregados):,}")
print(f"Entregas en 48 horas o menos: {tasa_a_tiempo:.1f}%")
print(
    "P90 del tiempo total de entrega:",
    f"{p90_entrega_total:.2f} horas",
)


# Marcamos si cada pedido entregado cumplió la promesa de 48 horas.
entregas_resumen = entregados.copy()
entregas_resumen["a_tiempo"] = (
    entregas_resumen["lead_time_h"] <= 48
)


# Comparamos servicio, mediana de transporte y P90 por transportista.
por_transportista = (
    entregas_resumen
    .dropna(subset=["carrier"])
    .groupby("carrier")
    .agg(
        pedidos=("lead_time_h", "size"),
        a_tiempo_pct=(
            "a_tiempo",
            lambda valores: 100 * valores.mean(),
        ),
        p50_transporte_h=(
            "transporte_h",
            lambda valores: valores.quantile(0.50),
        ),
        p90_transporte_h=(
            "transporte_h",
            lambda valores: valores.quantile(0.90),
        ),
        p90_total_h=(
            "lead_time_h",
            lambda valores: valores.quantile(0.90),
        ),
    )
    .round(1)
    .sort_values("a_tiempo_pct")
)

print("\nServicio y tiempos por transportista:")
print(por_transportista.to_string())


# Comparamos el P90 de empaquetado entre almacenes.
por_almacen = (
    entregas_resumen
    .dropna(subset=["warehouse", "packing_h"])
    .groupby("warehouse")
    .agg(
        pedidos=("packing_h", "size"),
        p90_packing_h=(
            "packing_h",
            lambda valores: valores.quantile(0.90),
        ),
    )
    .round(1)
    .sort_values("p90_packing_h", ascending=False)
)

print("\nP90 de empaquetado por almacén:")
print(por_almacen.to_string())


# Calculamos el P90 de cada etapa para cada almacén.
p90_por_almacen = (
    flujo
    .groupby("warehouse")[etapas]
    .quantile(0.90)
    .round(1)
)

print("\nP90 de cada etapa por almacén (horas):")
print(p90_por_almacen.to_string())