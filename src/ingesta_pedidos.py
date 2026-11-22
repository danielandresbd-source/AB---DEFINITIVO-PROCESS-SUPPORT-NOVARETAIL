from pathlib import Path

import pandas as pd
import yaml


# 1. Rutas del proyecto y de los archivos
RAIZ_PROYECTO = Path(__file__).resolve().parents[1]

RUTA_PEDIDOS = RAIZ_PROYECTO / "data" / "orders_raw.csv"
RUTA_CONTRATO = RAIZ_PROYECTO / "data" / "contrato_pedidos.yml"


# 2. Comprueba que existan los archivos necesarios
if not RUTA_PEDIDOS.exists():
    raise FileNotFoundError(f"No encuentro el archivo de pedidos: {RUTA_PEDIDOS}")

if not RUTA_CONTRATO.exists():
    raise FileNotFoundError(f"No encuentro el contrato: {RUTA_CONTRATO}")


# 3. Carga el contrato YAML
with open(RUTA_CONTRATO, "r", encoding="utf-8") as archivo:
    contrato = yaml.safe_load(archivo)

if not contrato or "fields" not in contrato or "contract" not in contrato:
    raise ValueError("El archivo YAML no tiene la estructura esperada.")


# 4. Lee el CSV original
#    Primero lo leemos sin modificar el archivo de entrada.
pedidos = pd.read_csv(RUTA_PEDIDOS)


# 5. Comprueba que el CSV y el contrato tengan las columnas esperadas
campos_esperados = set(contrato["fields"].keys())
columnas_csv = set(pedidos.columns)

columnas_faltantes = campos_esperados - columnas_csv
columnas_no_declaradas = columnas_csv - campos_esperados

if columnas_faltantes:
    raise ValueError(
        f"Al CSV le faltan columnas del contrato: {sorted(columnas_faltantes)}"
    )

if columnas_no_declaradas:
    print(
        "Aviso: el CSV contiene columnas que no aparecen en el contrato:",
        sorted(columnas_no_declaradas),
    )

print(
    f"Contrato leído: {contrato['contract']['name']} "
    f"v{contrato['contract']['version']}"
)
print(f"Campos declarados en el contrato: {len(campos_esperados)}")
print("Columnas del CSV presentes en el contrato: sí")


# 6. Convierte las columnas de fecha a fecha/hora
#    Los valores que no se puedan interpretar se convierten en NaT
#    y las reglas de calidad podrán detectarlos.
columnas_fecha = ["created_ts", "promised_ts", "delivered_ts"]

for columna in columnas_fecha:
    pedidos[columna] = pd.to_datetime(pedidos[columna], errors="coerce")


# 7. Perfil inicial del conjunto original
print(f"\nArchivo leído: {RUTA_PEDIDOS.name}")
print(f"Filas: {len(pedidos):,}")
print(f"Pedidos únicos: {pedidos['order_id'].nunique():,}")

print(
    "Filas cuyo order_id está duplicado:",
    int(pedidos.duplicated("order_id", keep=False).sum()),
)

print("\nValores vacíos por columna:")
print(pedidos.isna().sum())

print("\nPrimeras cinco filas:")
print(pedidos.head().to_string(index=False))


# 8. Crea una copia de trabajo para validar
#    La tabla pedidos original se conserva en memoria sin las columnas de control.
revision = pedidos.copy()


# 9. Reglas de calidad
#    Campos, patrón, dominios y mínimos se leen del contrato YAML.
#    Las reglas de negocio entre fechas, costes y estado (quality_rules)
#    están codificadas aquí siguiendo lo que describe el contrato.


# Campos obligatorios: se consultan en el YAML.
campos_obligatorios = [
    campo
    for campo, definicion in contrato["fields"].items()
    if not definicion.get("nullable", False)
]

revision["campos_obligatorios_presentes"] = (
    revision[campos_obligatorios].notna().all(axis=1)
)


# El patrón del identificador se obtiene del contrato.
patron_id = contrato["fields"]["order_id"]["pattern"]

revision["id_valido"] = (
    revision["order_id"]
    .astype("string")
    .str.fullmatch(patron_id, na=False)
)


# La clave primaria también se obtiene del contrato.
campos_clave = contrato["contract"]["primary_key"]

revision["clave_unica"] = ~revision.duplicated(
    subset=campos_clave,
    keep=False,
)


# Los dominios permitidos se leen del YAML.
revision["tienda_valida"] = revision["store"].isin(
    contrato["fields"]["store"]["allowed_values"]
)

revision["canal_valido"] = revision["channel"].isin(
    contrato["fields"]["channel"]["allowed_values"]
)

revision["estado_valido"] = revision["status"].isin(
    contrato["fields"]["status"]["allowed_values"]
)


# El mínimo de ingresos se obtiene del contrato.
minimo_ingresos = contrato["fields"]["revenue_eur"]["minimum"]

revision["ingreso_valido"] = revision["revenue_eur"].ge(minimo_ingresos)


# El mínimo del coste se obtiene del contrato.
# Además, el coste no puede superar los ingresos.
minimo_coste = contrato["fields"]["variable_cost_eur"]["minimum"]

revision["coste_valido"] = (
    revision["variable_cost_eur"].notna()
    & revision["variable_cost_eur"].ge(minimo_coste)
    & revision["variable_cost_eur"].le(revision["revenue_eur"])
)


# Reglas de coherencia entre las fechas y el estado del pedido.
revision["promesa_valida"] = (
    revision["promised_ts"] >= revision["created_ts"]
)

revision["fecha_entrega_valida"] = (
    revision["delivered_ts"].isna()
    | (revision["delivered_ts"] >= revision["created_ts"])
)

revision["estado_coherente"] = (
    (
        (revision["status"] == "delivered")
        & revision["delivered_ts"].notna()
    )
    |
    (
        (revision["status"] != "delivered")
        & revision["delivered_ts"].isna()
    )
)


# 10. Lista de reglas que cada fila debe superar
reglas = [
    "campos_obligatorios_presentes",
    "id_valido",
    "clave_unica",
    "tienda_valida",
    "canal_valido",
    "estado_valido",
    "promesa_valida",
    "ingreso_valido",
    "coste_valido",
    "fecha_entrega_valida",
    "estado_coherente",
]


# Una fila es válida si cumple todas las reglas.
revision["calidad_ok"] = revision[reglas].all(axis=1)


# 11. Separa filas válidas y filas en cuarentena
pedidos_validos = revision.loc[revision["calidad_ok"]].copy()
cuarentena = revision.loc[~revision["calidad_ok"]].copy()


# Añade a cada fila en cuarentena las reglas que incumple.
cuarentena["motivos_fallo"] = cuarentena.apply(
    lambda fila: ", ".join(
        regla for regla in reglas if not bool(fila[regla])
    ),
    axis=1,
)


# 12. Muestra el resultado de las reglas
print("\nResultado de la validación:")

for regla in reglas:
    fallos = int((~revision[regla]).sum())
    print(f"{regla}: {fallos} filas no cumplen la regla")

print(f"\nFilas válidas: {len(pedidos_validos):,}")
print(f"Filas en cuarentena: {len(cuarentena):,}")


# 13. Guarda las salidas sin sobrescribir el CSV original
CARPETA_SALIDA = RAIZ_PROYECTO / "outputs"
CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)

RUTA_VALIDOS = CARPETA_SALIDA / "pedidos_validos.csv"
RUTA_CUARENTENA = CARPETA_SALIDA / "pedidos_cuarentena.csv"

pedidos_validos.to_csv(RUTA_VALIDOS, index=False)
cuarentena.to_csv(RUTA_CUARENTENA, index=False)

print("\nArchivos guardados:")
print(f"Válidos: {RUTA_VALIDOS}")
print(f"Cuarentena: {RUTA_CUARENTENA}")


# 14. Calcula indicadores para comparar el conjunto original y el validado
def calcular_kpis(df):
    entregados = df["status"].eq("delivered")

    entregas_en_plazo = (
        entregados
        & df["delivered_ts"].le(df["promised_ts"])
    )

    ingresos = df["revenue_eur"].sum(min_count=1)
    costes = df["variable_cost_eur"].sum(min_count=1)
    margen = ingresos - costes

    return pd.Series({
        "filas": len(df),
        "pedidos_unicos": df["order_id"].nunique(),
        "entregados": int(entregados.sum()),
        "entregas_en_plazo_pct": (
            100 * entregas_en_plazo.sum() / entregados.sum()
            if entregados.sum() > 0
            else 0
        ),
        "cancelaciones_pct": (
            100 * df["status"].eq("cancelled").mean()
        ),
        "ingresos_eur": ingresos,
        "costes_variables_eur": costes,
        "margen_bruto_eur": margen,
        "margen_bruto_pct": (
            100 * margen / ingresos
            if pd.notna(ingresos) and ingresos != 0
            else 0
        ),
    })


comparacion_kpis = pd.DataFrame({
    "Original sin validar": calcular_kpis(pedidos),
    "Solo filas validadas": calcular_kpis(pedidos_validos),
})

print("\nComparación de indicadores:")
print(comparacion_kpis.round(2).to_string())