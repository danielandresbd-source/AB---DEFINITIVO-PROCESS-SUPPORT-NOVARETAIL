from pathlib import Path
import re

import pandas as pd


# Localiza las carpetas del proyecto.
RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
RUTA_TICKETS = RAIZ_PROYECTO / "data" / "tickets_raw.csv"
CARPETA_SALIDA = RAIZ_PROYECTO / "outputs"

if not RUTA_TICKETS.exists():
    raise FileNotFoundError(f"No encuentro el archivo: {RUTA_TICKETS}")


# Patrones didácticos del laboratorio para localizar PII en el texto.
EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

PHONE_RE = re.compile(
    r"(?<!\d)(?:\+34\s*)?[6789](?:[\s-]*\d){8}(?!\d)"
)

ORDER_RE = re.compile(
    r"\bORD-\d{6}\b",
    flags=re.IGNORECASE,
)


# Lee los tickets.
tickets = pd.read_csv(
    RUTA_TICKETS,
    parse_dates=["created_ts"],
)

# Comprueba que estén las columnas que genera el laboratorio.
columnas_esperadas = {
    "ticket_id",
    "created_ts",
    "channel",
    "text",
    "true_topic",
}

columnas_faltantes = columnas_esperadas - set(tickets.columns)

if columnas_faltantes:
    raise ValueError(
        f"Al CSV le faltan columnas: {sorted(columnas_faltantes)}"
    )


# Busca cada tipo de dato dentro del texto libre.
tickets["tiene_email"] = tickets["text"].str.contains(
    EMAIL_RE,
    na=False,
)

tickets["tiene_telefono"] = tickets["text"].str.contains(
    PHONE_RE,
    na=False,
)

tickets["tiene_order_id"] = tickets["text"].str.contains(
    ORDER_RE,
    na=False,
)


# Extrae el identificador del pedido para poder analizarlo aparte del texto.
def extraer_order_id(texto):
    coincidencia = ORDER_RE.search(str(texto))
    return coincidencia.group(0).upper() if coincidencia else None


tickets["order_id"] = tickets["text"].map(extraer_order_id)


# Enmascara correo, teléfono e identificador de pedido dentro del texto.
# El order_id se conserva solo en su columna aparte (acceso controlado),
# para que el texto que ve Analítica no lo incluya.
def enmascarar_pii(texto):
    texto = EMAIL_RE.sub("[EMAIL]", str(texto))
    texto = PHONE_RE.sub("[PHONE]", texto)
    texto = ORDER_RE.sub("[ORDER_ID]", texto)
    return texto


tickets["text_masked"] = tickets["text"].map(enmascarar_pii)


# Muestra conteos agregados; no imprime el texto original ni los datos detectados.
print(f"Tickets leídos: {len(tickets):,}")
print(
    "Tickets con correo:",
    int(tickets["tiene_email"].sum()),
)
print(
    "Tickets con teléfono:",
    int(tickets["tiene_telefono"].sum()),
)
print(
    "Tickets con identificador de pedido:",
    int(tickets["tiene_order_id"].sum()),
)
print(
    "Textos exactamente duplicados:",
    int(tickets["text"].duplicated().sum()),
)


# Guarda para análisis una versión que no contiene el texto original.
# El order_id se conserva porque puede hacer falta para relacionar el ticket
# con su pedido; sigue siendo un identificador enlazable y debe protegerse.
CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)

RUTA_ENMASCARADOS = CARPETA_SALIDA / "tickets_analitica.csv"

columnas_analiticas = [
    "ticket_id",
    "created_ts",
    "channel",
    "order_id",
    "text_masked",
    "true_topic",
]

tickets[columnas_analiticas].to_csv(
    RUTA_ENMASCARADOS,
    index=False,
)

print(f"\nCopia para análisis guardada en: {RUTA_ENMASCARADOS}")