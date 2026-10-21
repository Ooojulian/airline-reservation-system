# Explicación: datos crudos, job EMR (PySpark) y tablas Glue/Athena

Equipo: Julian Cristancho, Andrés Urrego. Seed 2682. Cuenta 930581488240, us-east-1.

## 1. Qué se hizo (resumen)
1. Se generó el histórico sucio con `python -m airline_data.generate --seed 2682 --rows 2000000` en la EC2
   (venv `/opt/p2venv`) y se subió a `s3://airline-system-930581488240/raw/` (flights.csv 0,4 MB, reservations.csv 82 MB).
   Muestra de prueba en `raw-sample/` (todos los vuelos + 1 de cada 20 reservas, 5 %).
2. El oráculo (`load_truth(2682, 2000000)`) dio los valores esperados: `evidence/p2/oraculo_esperado.json`.
3. Job `emr/curate.py` (PySpark) en un clúster EMR efímero lanzado con `emr/run_emr.sh`. Escribe
   `curated/reservations_curated/month=YYYY-MM/` y `curated/route_month_metrics/` en Parquet.
4. Tablas en Glue Data Catalog creadas con `emr/athena_tables.sql` + `emr/create_tables.sh`.
5. Verificación con Athena contra el oráculo: `evidence/p2/verificacion_analytics.txt` (todo coincide exacto).

## 2. Qué hace el job, regla por regla
Se lee todo como texto (sin inferSchema) para controlar los casts nosotros: `price_cents` puede venir vacío o como
decimal, y los ids no deben quedar como `123.0`.
- **C1 route**: `upper(regexp_replace(route, '\s+', ''))` sobre flights. `' bog - mde '` -> `BOG-MDE`.
- **C2 status**: `upper(trim(status))`. `'confirmed '` -> `CONFIRMED`.
- **C3**: se filtra `status IN ('CONFIRMED','CANCELLED')` (se descartan los `PENDING`).
- **C4**: `price_cents` se castea a double; se descarta nulo o `<= 0` (cubre vacíos, 0, -1, -500); luego se castea a bigint.
- **C5**: `dropDuplicates(["reservation_id"])`. Las duplicadas son copias exactas, así que da igual cuál se conserve.
- **Join inner** con vuelos limpios por `flight_id`; trae `route` (ya limpia) y `month = substring(flight_date,1,7)`.
- **reservations_curated**: columnas en orden reservation_id, flight_id, route, fare_class, price_cents(bigint), status,
  partición `month`. `repartition("month")` antes de escribir evita miles de archivos pequeños: queda 1 archivo por mes.
- **route_month_metrics**: `seats_capacity` = suma de `capacity` de los vuelos de la ruta en el mes (se calcula desde
  vuelos, no desde reservas, para contar vuelos sin ventas); `seats_sold` = CONFIRMED; `revenue_cents` = suma de
  price de CONFIRMED; `occupancy = seats_sold/seats_capacity` (double); `bookings` = CONFIRMED
  (según el contrato DATA.md); `cancellations` = CANCELLED. `coalesce(1)` -> un solo archivo (tabla pequeña, 9 KB).
- Declaración honesta: el contrato textual (DATA.md) dice `bookings` = solo CONFIRMED, pero `oracle.py` usa
  CONFIRMED + CANCELLED. Seguimos al contrato escrito; ningún test revisa `bookings`.

Resultado: 2.000.542 filas curadas, 24 particiones, idéntico al oráculo.

## 3. Por qué Parquet y por qué partición por mes
- **Parquet** es columnar y comprimido: Athena lee solo las columnas que usa la consulta y cobra por bytes leídos.
  El CSV crudo pesa 82 MB; el Parquet del detalle pesa 19,5 MB (24 archivos, ~812 KB cada uno).
- **Partición por mes** (`month=YYYY-MM`): las consultas casi siempre filtran por mes; Athena solo abre la carpeta de ese mes.
  Consulta de un mes (`WHERE month='2024-01'`, count + sum(price_cents)): **308.482 B** escaneados.
  Límite del harness: 2 x tamaño promedio de partición = 2 x 811.613 = 1.623.226 B. Cumple con margen.
- **Sin partición** la misma consulta tendría que leer las columnas `price_cents` de los 24 meses: ~24 x 308 KB ≈ 7,4 MB
  (y si fuera `SELECT *`, los 19,5 MB completos; sobre el CSV crudo, 82 MB). Es decir ~24 veces más bytes y más costo/tiempo.
  (Cifra sin partición estimada a partir de lo medido por mes; no se ejecutó esa consulta.)
- **Partition projection**: en lugar de registrar 24 particiones en Glue, la tabla declara el rango
  (`projection.month.type=date`, `format=yyyy-MM`, `range=2023-01,2024-12`, plantilla de ubicación). Athena calcula las
  rutas sin consultar el catálogo, no hay que correr `MSCK REPAIR TABLE` y no se desactualiza.

## 4. Por qué EMR con clúster efímero
- Spark distribuye la limpieza (join, dedup global) sobre varios nodos; el volumen (2 M filas) y, sobre todo, el
  requisito del curso exigen un job de big data reproducible, no un script local.
- Un clúster EMR cuesta mientras está encendido. Como el trabajo es por lotes, se crea el clúster, corre un step y se
  destruye: se paga solo por ~10 minutos. Los datos viven en S3 (desacoplado del cómputo), así que no se pierde nada al apagar.
- Configuración: emr-7.14.0, Spark, 1 master + 2 core `m4.large` (único tipo permitido), roles `EMR_DefaultRole` /
  `EMR_EC2_DefaultRole`, subnet us-east-1a, logs en `s3://.../emr/logs/`. Step `spark-submit --deploy-mode cluster`.

## 5. Cómo se garantiza el apagado (R10)
- `--auto-terminate`: el clúster se apaga solo cuando terminan todos los steps.
- `ActionOnFailure=TERMINATE_CLUSTER`: si el step falla, el clúster también se apaga (nunca queda esperando).
- `run_emr.sh` espera `aws emr wait cluster-terminated` y al final lista `aws emr list-clusters --active`; salió vacío.
- Solo m4.large, sin NAT, sin otros tipos de instancia.

## 6. Horas normalizadas usadas
Un m4.large = 4 horas normalizadas por hora de instancia; 3 nodos facturan al menos 1 hora cada uno = 12 por clúster.
| Clúster | Corrida | Estado | NormalizedInstanceHours |
|---|---|---|---|
| j-0600594649O6OD8KSHE | muestra (raw-sample -> curated-sample) | TERMINATED, step COMPLETED | 12 |
| j-06661302ZS6EJJ3Z0HBI | completa (raw -> curated) | TERMINATED, step COMPLETED | 12 |
Total: **24 h normalizadas** de un límite de 150.
La corrida completa es la última y sobrescribió `curated/` entero, así que todos los Parquet se escribieron durante
un run COMPLETED (R4 compara LastModified con la ventana del run).

## 7. Preguntas probables en la sustentación
- **¿Por qué lees todo como string?** Para evitar que Spark infiera mal: `price_cents` vacío o decimal convertiría la
  columna a double y los ids a `123.0`. Casteamos explícitamente a bigint/string.
- **¿Qué pasa con los duplicados?** Son copias exactas del mismo `reservation_id`; `dropDuplicates` conserva una.
  Verificado: 2.000.542 filas = oráculo.
- **¿Por qué la capacidad sale de vuelos y no de reservas?** Un vuelo sin ventas igual aporta asientos; calcularla desde
  reservas subestimaría la capacidad y sobrestimaría la ocupación.
- **¿Por qué `bookings` solo cuenta confirmadas?** Así lo dice el contrato escrito (DATA.md); el oráculo cuenta también
  las canceladas, pero ningún test lo revisa; lo declaramos.
- **¿Por qué `repartition("month")`?** Sin eso cada tarea escribe un archivo por mes (muchos archivos pequeños); con
  eso queda 1 archivo por partición.
- **¿Por qué projection y no `MSCK REPAIR`/crawler?** Es determinista, sin costo de crawler ni mantenimiento y Athena
  poda más rápido. Limitación: hay que ampliar el rango si llegan meses fuera de 2023-01..2024-12.
- **¿Cómo sabes que no queda nada encendido?** `list-clusters --active` vacío y ambos clústeres TERMINATED con
  "Steps completed".
- **¿Por qué EMR y no Athena CTAS o Glue?** El curso pide Spark en EMR; permite controlar las reglas y la escritura
  particionada. El costo se controla con clúster efímero.
- **¿Cuánto costó?** 24 horas normalizadas (~US$ 1-2 en EMR + EC2 subyacente), muy por debajo del límite de 150.
- **¿Qué hace `coalesce(1)` en métricas?** Es una tabla de ~240 filas (10 rutas x 24 meses); un archivo evita
  sobrecarga de archivos diminutos.
