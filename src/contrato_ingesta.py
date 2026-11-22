from pathlib import Path

import pandas as pd
import yaml


# 1. Localiza los archivos del proyecto
RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
RUTA_CONTRATO = RAIZ_PROYECTO / "data" / "contrato_meteo.yml"
RUTA_METEO = RAIZ_PROYECTO / "data" / "meteo_para_validar.csv"
CARPETA_SALIDA = RAIZ_PROYECTO / "outputs"


# 2. Comprueba que existan el contrato y el CSV
if not RUTA_CONTRATO.exists():
    raise FileNotFoundError(f"No encuentro el contrato: {RUTA_CONTRATO}")

if not RUTA_METEO.exists():
    raise FileNotFoundError(f"No encuentro el CSV: {RUTA_METEO}")


# 3. Lee el contrato YAML
with open(RUTA_CONTRATO, "r", encoding="utf-8") as archivo:
    contrato = yaml.safe_load(archivo)

if not contrato or "columnas" not in contrato:
    raise ValueError("El contrato YAML no tiene la estructura esperada.")


# 4. Lee el CSV original sin sobrescribirlo
meteo_raw = pd.read_csv(RUTA_METEO)

columnas_esperadas = set(contrato["columnas"].keys())
columnas_csv = set(meteo_raw.columns)

columnas_faltantes = columnas_esperadas - columnas_csv

if columnas_faltantes:
    raise ValueError(
        f"Al CSV le faltan columnas del contrato: {sorted(columnas_faltantes)}"
    )

print(f"Contrato: {contrato['nombre']} v{contrato['version']}")
print(f"Proveedor declarado: {contrato['proveedor']}")
print(f"Registros recibidos: {len(meteo_raw):,}")
print(f"Columnas presentes: {len(columnas_esperadas)}")


# 5. Crea una copia de trabajo y prepara el registro de reglas
revision = meteo_raw.copy()
reglas = []


def agregar_regla(nombre, condicion):
    """Guarda el resultado de una regla como una columna booleana."""
    revision[nombre] = (
        pd.Series(condicion, index=revision.index)
        .fillna(False)
        .astype(bool)
    )
    reglas.append(nombre)


# 6. Convierte y valida los tipos declarados en el contrato
for columna, definicion in contrato["columnas"].items():
    valor_original = meteo_raw[columna]

    if definicion["tipo"] == "fecha":
        valor_convertido = pd.to_datetime(
            valor_original,
            errors="coerce",
        )
        revision[columna] = valor_convertido

        agregar_regla(
            f"{columna}_tipo_valido",
            valor_original.isna() | valor_convertido.notna(),
        )

    elif definicion["tipo"] == "decimal":
        valor_convertido = pd.to_numeric(
            valor_original,
            errors="coerce",
        )
        revision[columna] = valor_convertido

        agregar_regla(
            f"{columna}_tipo_valido",
            valor_original.isna() | valor_convertido.notna(),
        )


# 7. Valida nulabilidad, dominios y rangos del contrato
for columna, definicion in contrato["columnas"].items():

    # Si el campo no admite nulos, cada fila debe tener valor.
    if not definicion["nulo"]:
        agregar_regla(
            f"{columna}_no_nulo",
            revision[columna].notna(),
        )

    # Si el contrato limita los valores posibles, comprueba el dominio.
    if "valores" in definicion:
        dentro_del_dominio = revision[columna].isin(definicion["valores"])

        if definicion["nulo"]:
            dentro_del_dominio = (
                dentro_del_dominio | revision[columna].isna()
            )

        agregar_regla(
            f"{columna}_dominio_valido",
            dentro_del_dominio,
        )

    # Si el contrato define mínimos y máximos, valida el rango.
    if "min" in definicion or "max" in definicion:
        dentro_del_rango = pd.Series(
            True,
            index=revision.index,
        )

        if "min" in definicion:
            dentro_del_rango &= revision[columna].ge(definicion["min"])

        if "max" in definicion:
            dentro_del_rango &= revision[columna].le(definicion["max"])

        if definicion["nulo"]:
            dentro_del_rango = (
                dentro_del_rango | revision[columna].isna()
            )

        agregar_regla(
            f"{columna}_rango_valido",
            dentro_del_rango,
        )


# 8. Comprueba que la clave ciudad + fecha sea única
clave = contrato["clave"]

agregar_regla(
    "clave_unica",
    ~revision.duplicated(subset=clave, keep=False),
)


# 9. Aplica la regla de negocio entre temperatura mínima y máxima
if "tmin_c <= tmax_c" in contrato.get("reglas", []):
    agregar_regla(
        "temperatura_minima_no_supera_maxima",
        revision["tmin_c"] <= revision["tmax_c"],
    )


# 10. Marca la fila como válida solo si supera todas las reglas
revision["calidad_ok"] = revision[reglas].all(axis=1)


def obtener_motivos_fallo(fila):
    """Devuelve las reglas que incumple una fila."""
    return "; ".join(
        regla for regla in reglas if not bool(fila[regla])
    )


revision["motivos_fallo"] = revision.apply(
    obtener_motivos_fallo,
    axis=1,
)


# 11. Separa registros válidos y registros en cuarentena
meteo_validos = revision.loc[revision["calidad_ok"]].copy()
meteo_cuarentena = revision.loc[~revision["calidad_ok"]].copy()


# 12. Muestra los resultados
print("\nFallos por regla:")

for regla in reglas:
    fallos = int((~revision[regla]).sum())
    if fallos > 0:
        print(f"{regla}: {fallos} filas")

print(f"\nFilas válidas: {len(meteo_validos):,}")
print(f"Filas en cuarentena: {len(meteo_cuarentena):,}")


# Este CSV es histórico de agosto.
# El SLA de frescura de siete días se comprobaría en una ingesta diaria
# de producción, no contra esta muestra histórica.
print(
    "\nSLA de frescura declarado:",
    contrato.get("frescura_max_dias"),
    "días; no se aplica a esta muestra histórica.",
)


# 13. Guarda los resultados sin modificar el archivo original
CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)

RUTA_VALIDOS = CARPETA_SALIDA / "meteo_validos.csv"
RUTA_CUARENTENA = CARPETA_SALIDA / "meteo_cuarentena.csv"

meteo_validos.to_csv(RUTA_VALIDOS, index=False)
meteo_cuarentena.to_csv(RUTA_CUARENTENA, index=False)

print("\nArchivos guardados:")
print(f"Válidos: {RUTA_VALIDOS}")
print(f"Cuarentena: {RUTA_CUARENTENA}")