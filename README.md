# NovaRetail – Entregable 1: Diagnóstico del proceso y de sus riesgos

Unit 17 – Business Process Support (Pearson HND, MSMK). Curso 2026-2027.
Autores: Daniel Andres Brito Durán y Julian Danilo Brito Durán
## Estructura

```
data/       Datos de entrada (no se modifican) y contratos YAML
src/        Scripts de ingesta, validación, event log y privacidad
outputs/    Ficheros que generan los scripts
```

## Cómo ejecutarlo

Requiere [uv](https://docs.astral.sh/uv/). Desde la raíz del repositorio:

```bash
uv sync
uv run python src/ingesta_pedidos.py
uv run python src/contrato_ingesta.py
uv run python src/privacidad_tickets.py
uv run python src/d1_operativa.py
uv run python src/kpis_deduplicados.py
uv run python src/meteo_event_log.py
```

El orden importa en dos casos: `kpis_deduplicados.py` necesita `outputs/pedidos_validos.csv`
(lo genera `ingesta_pedidos.py`) y `meteo_event_log.py` necesita `outputs/meteo_validos.csv`
(lo genera `contrato_ingesta.py`).

## Qué hace cada script

| Script | Qué hace | Salida |
| --- | --- | --- |
| `ingesta_pedidos.py` | Valida `orders_raw.csv` con `contrato_pedidos.yml`, separa válidos y cuarentena y compara KPI originales y validados | `pedidos_validos.csv`, `pedidos_cuarentena.csv` |
| `kpis_deduplicados.py` | Conserva una copia por pedido, aplica el resto de reglas y añade la columna "validado y deduplicado" | `kpis_comparacion.csv` |
| `contrato_ingesta.py` | Valida la muestra meteorológica con `contrato_meteo.yml` | `meteo_validos.csv`, `meteo_cuarentena.csv` |
| `privacidad_tickets.py` | Cuenta datos personales en los tickets y enmascara correo, teléfono e identificador de pedido en el texto | `tickets_analitica.csv` |
| `d1_operativa.py` | Limpia el event log, calcula P50/P90 por etapa, WIP, regla base P90 y regla Q75 | Resultados por pantalla |
| `meteo_event_log.py` | Cruza el event log con la meteorología por almacén y fecha (solo agregados) | `meteo_event_log_resumen.csv` |

## Notas y limitaciones

- Los contratos YAML definen campos, patrones, dominios y límites que los scripts leen. Las reglas
  de negocio entre fechas, costes y estado están codificadas en `ingesta_pedidos.py`.
- El event log no incluye fecha prometida: en `d1_operativa.py` se **supone** una promesa de
  48 horas desde la creación.
- `orders_raw.csv` (identificadores `ORD-`) y `event_log.csv` (identificadores `NR-`) no tienen una
  clave común validada, así que no se han unido.
- `data/tickets_raw.csv` contiene datos personales sintéticos. En un caso real, el original
  tendría acceso restringido y solo se compartiría `tickets_analitica.csv`.
- El texto de `tickets_analitica.csv` enmascara correo, teléfono y pedido con expresiones regulares
  básicas; no detecta nombres ni direcciones.
