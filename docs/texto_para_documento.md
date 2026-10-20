# Texto para el documento del Parcial 1

Fragmentos listos para pegar. Cada bloque indica la sección donde va y si **reemplaza** o **se agrega**.
Las cifras salen de la ejecución del 19 de octubre de 2026 en AWS (evidencias en `evidence/`).

---

## A. Sección 10 nueva — Arquitectura de frontend (se agrega entre la 9 y la 11)

### 10. Arquitectura de frontend

#### 10.1 SPA servida como archivos estáticos

Se adopta una aplicación de una sola página (SPA) que consume la misma API REST de la sección 9.2. Se construye como archivos estáticos (HTML, JS y CSS) y la sirve un contenedor nginx en la misma EC2 de la API (puerto 3000). No se agrega un servicio de AWS adicional.

| Alternativa | Comportamiento | Decisión |
|---|---|---|
| Renderizado en servidor (plantillas en FastAPI) | Cada búsqueda recarga la página completa y el servidor de la API también genera HTML. Compite por CPU con las reservas en los picos. | Descartada |
| SPA servida desde S3 + CloudFront | Buena latencia global, pero suma un servicio facturable. La aerolínea es regional y no lo necesita. | Descartada para esta versión |
| **SPA estática en la misma EC2 (nginx)** | Después de la primera carga solo viajan los datos JSON de la búsqueda. El servidor de la API solo atiende JSON. | **Adoptada** |

Justificación: el RNF-01 (búsqueda con p95 ≤ 1 s) se mide sobre la respuesta de `/flights/search`. Con una SPA la búsqueda es una sola petición JSON y la pantalla se actualiza sin recargar, así que el usuario percibe casi el mismo tiempo que mide la API (p95 = 83 ms en la prueba de la sección 9.4). El SEO no es crítico: los resultados de vuelos dependen de la fecha y no se indexan.

#### 10.2 Aviso cuando ya no hay silla

El sistema no tiene mapa de asientos (EXC-02). Lo que puede agotarse mientras el usuario decide es el **cupo del vuelo**, no un asiento concreto. Por eso no se usan WebSockets ni notificaciones push:

- **En la reserva (garantía fuerte):** `POST /reservations` responde **409** de forma síncrona si otro cliente tomó la última silla (sección 8.2). La SPA muestra "Este vuelo se agotó mientras completabas la reserva" y repite la búsqueda automáticamente con los mismos filtros.
- **Mientras el usuario llena sus datos (aviso blando):** la SPA consulta `GET /flights/{flight_id}` cada 30 s y, si `seats_available` llega a 0, deshabilita el botón de confirmar.
- **Por qué no WebSockets:** mantener conexiones abiertas por cada usuario en temporada alta aumenta la carga y el costo de la EC2. Además, el aviso nunca podría ser la garantía: la única fuente de verdad es el `UPDATE` condicional (RN-01). El 409 cubre el caso que importa al mismo costo de una petición normal.

#### 10.3 Agencias aliadas

No se construye una interfaz separada en esta versión. Las agencias usan la misma API (sección 8.3, EXC-03), y sus agentes pueden usar la misma SPA o integrarse directamente por API. Un portal B2B con login de agencia, comisiones y crédito queda como evolución. Sería otra SPA sobre la misma API, sin tocar el núcleo transaccional.

#### 10.4 Monedas e idiomas

- **Moneda:** la aerolínea cobra solo en COP (SUP-01). El backend devuelve montos enteros en centavos (`price_cents`) y la SPA solo los formatea con `Intl.NumberFormat('es-CO', {style:'currency', currency:'COP'})`. Si en el futuro hubiera varias monedas, la conversión viviría en el backend (tabla de tasas y precio congelado por tramo, RNF-08) y no en el navegador: un precio calculado en el cliente no sería auditable.
- **Idioma:** los textos se separan en catálogos de mensajes (`es.json`, `en.json`) desde el inicio, aunque solo se publique español. Agregar inglés es agregar un archivo, no reescribir pantallas. Las fechas se envían a la API en ISO `YYYY-MM-DD` y se formatean según el idioma del navegador.

---

## B. Sección 11.4 — Escenario de crecimiento ×10 (se agrega al final de la 11.4)

> Antes de este bloque, reemplace la tabla de la 11.4 por la versión del punto C.6.

#### Escenario de crecimiento ×10

| Escenario | Qué cambia | Costo mensual 24/7 estimado | Decisión |
|---|---|---|---|
| Base actual | 150 mil reservas, ETL diario | ~US$32 | — |
| **Volumen ×10** (1,5 M reservas, ~100 MB → ~1 GB) | db.t3.micro (1 GB de RAM) ya no mantiene en memoria el índice y las tablas calientes. El job de Glue tarda ~10 veces más (~15 min). | ~US$50: RDS pasa a db.t3.small (+US$12), Glue ~US$6 | Subir a db.t3.small. El job sigue siendo diario. |
| **Frecuencia ×10** (ETL ~10 veces al día) | Mismo volumen, 300 ejecuciones al mes | ~US$40: Glue pasa de ~US$0,8 a ~US$8 | No se justifica: SUP-09 tolera un día de antigüedad y el tablero gerencial no gana nada. |
| **Ambos ×10** | 300 ejecuciones de ~15 min | ~US$90: Glue ~US$53 | Se cambia el diseño del ETL: carga **incremental** con marca de agua en `updated_at`, que procesa solo los cambios del día y vuelve a dejar el job en minutos. |

Relación con los pilares:

- **Optimización de costos:** el costo crece por dos palancas distintas. El volumen se resuelve con *right-sizing* de la base, un paso de micro a small y no un cluster. La frecuencia se resuelve rechazándola, porque el requisito (SUP-09) no la pide. En la versión actual el ETL recalcula todo cada vez, lo que es barato hoy (~US$0,03 por ejecución) pero escala linealmente: es la primera pieza que se rediseña al crecer.
- **Sostenibilidad:** correr el ETL cada hora multiplicaría por 24 el cómputo consumido sin cambiar ninguna decisión del negocio. Detener RDS y EC2 fuera de uso (`apagar.sh`) y procesar de forma incremental reducen la energía consumida por resultado útil. Como mejora adicional, migrar a instancias Graviton (t4g) reduce cerca de 20 % el costo y el consumo por el mismo rendimiento.

---

## C. Actualizaciones por el despliegue real

### C.1 Sección 8.1 — reemplazar la primera frase

> Se adopta un único servicio (FastAPI) sobre una única base transaccional (**Amazon RDS for PostgreSQL 16, instancia db.t3.micro**).

*(El resto del párrafo no cambia.)*

### C.2 Sección 6 — RNF-05 y RNF-07 (reemplazar la columna de verificación)

- **RNF-05:** 99 % mensual en el laboratorio (una instancia RDS en una zona). En producción, 99,9 % con **RDS Multi-AZ**. La caída en temporada alta es venta perdida, pero Multi-AZ duplica el costo de la base. Decisión documentada en la sección 11.4.
- **RNF-07:** primer cuello de botella: la instancia única **db.t3.micro** (1 GB de RAM, CPU con créditos). La contención no crece con las rutas porque es por vuelo. La siguiente talla es db.t3.small (escenario ×10, sección 11.4).

### C.3 Sección 9.1 — reemplazar la tabla de estructura

| Ruta | Contenido |
|---|---|
| backend/app/main.py | Endpoints de la API (FastAPI) y lógica de reserva con control de concurrencia. |
| backend/app/db.py | Pool de conexiones a RDS PostgreSQL (psycopg 3, hasta 20 conexiones por worker). |
| backend/schema.sql | Modelo entidad-relación implementado (10 tablas). |
| backend/schema_olap.sql | Tablas de hechos de la base analítica. |
| backend/seed.sql | Datos de ejemplo generados en SQL (8 rutas, 1.448 vuelos, 150.197 reservas). |
| backend/Dockerfile, backend/run_api.sh | Imagen de la API (Python 3.12, Uvicorn con 2 workers) y arranque leyendo credenciales de SSM. |
| tests/test_api.py | 11 pruebas (20 casos con las rondas de concurrencia). |
| tests/p95_search.py | Medición de latencia de búsqueda con 20 clientes. |
| infra/1_red_y_rds.sh | Security groups, RDS, parámetros SSM, bucket S3 y endpoint de S3. |
| infra/2_esquemas_y_datos.sh | Crea las dos bases y carga el esquema y los datos (se ejecuta en la EC2). |
| infra/3_ec2_api.sh | EC2, Docker y despliegue de la API. |
| infra/4_glue.sh | Conexiones JDBC, crawlers y job de Glue. |
| infra/apagar.sh, infra/encender.sh | Detienen y reinician RDS y EC2. |
| infra/glue/etl_oltp_to_olap.py | Job ETL de Glue. |
| infra/exported/ | Definiciones exportadas de los recursos de AWS (sin contraseñas). |
| evidence/ | Salidas de las pruebas, latencia y Glue. |

### C.4 Sección 9.3 — reemplazar el primer punto

- **En AWS:** desde CloudShell o cualquier terminal con AWS CLI, ejecutar en orden `infra/1_red_y_rds.sh`, `infra/3_ec2_api.sh` (crea la EC2 y ejecuta en ella `2_esquemas_y_datos.sh` mediante SSM Run Command) y `infra/4_glue.sh`. Ninguna base de datos ni prueba se ejecuta en el equipo local. Al terminar cada sesión: `infra/apagar.sh`; al volver: `infra/encender.sh`, que imprime la nueva IP pública de la API.

### C.5 Sección 9.4 — reemplazar el párrafo inicial y el final

**Inicial:**
> La prueba crea un vuelo y libera 50 reservas simultáneas mediante una barrera de hilos. Se repite 5 rondas con 1 silla y 5 rondas con 5 sillas. Resultado de la ejecución del 19 de octubre de 2026 **en AWS**: API en una EC2 t3.small (Docker, 2 workers de Uvicorn) sobre **RDS PostgreSQL 16.15 db.t3.micro**:

*(La tabla de rondas no cambia: 1 silla → 201=1 · 409=49 en las 5 rondas; 5 sillas → 201=5 · 409=45 en las 5 rondas.)*

**Final:**
> En ninguna ronda hubo respuestas 5xx, y al final cada vuelo quedó con seats_available = 0. La suite completa pasó: 11 pruebas y 20 casos, incluida la del itinerario con escala que falla en el segundo tramo. La búsqueda midió **p95 = 83 ms** (p50 = 41 ms) con 20 clientes concurrentes y 100 peticiones, frente al objetivo de 1 s del RNF-01. Con los datos de ejemplo (1.448 vuelos, **150.197 reservas**, 170.135 tramos, 18.063 canceladas) no hay ningún vuelo cuyas sillas confirmadas difieran de su inventario. Evidencia: `evidence/evidencia_pruebas.txt` y `evidence/p95_search.txt`.

*(Se elimina la frase "Pendiente: repetir la prueba contra la API en la EC2…".)*

### C.6 Sección 9.5 — filas que se agregan a la tabla de desviaciones

| Desviación | Motivo | Documento actualizado |
|---|---|---|
| Aurora PostgreSQL db.t3.medium se reemplazó por RDS PostgreSQL db.t3.micro. | Las pruebas muestran p95 de 83 ms y cero sobreventas con la instancia más pequeña, así que la talla mayor no se justifica. Aurora cuesta ~5 veces más y la siguiente fase del curso limita la base a clases micro/small. | Secciones 8.1, 11.1, 11.3 y 11.4 |
| `flights.flight_id` es texto (por ejemplo `AV101-20260601`) y lo asigna quien crea el vuelo. | Identificador legible y estable entre sistemas (agencias, ETL). Permite crear vuelos de prueba con un id conocido. | Diccionario de datos |
| `POST /reservations` recibe `passenger_name` (documento y correo opcionales). | Simplifica la integración de agencias. Los datos opcionales siguen guardándose en `passengers`. | Sección 9.2 |
| Endpoint adicional `GET /flights/{flight_id}`. | La búsqueda omite los vuelos sin sillas; las pruebas y el frontend (10.2) necesitan leer el inventario de un vuelo concreto. | Sección 9.2 |
| Pool de conexiones ampliado a 20 por worker. | Con 50 peticiones simultáneas, un pool pequeño hacía esperar o rechazar peticiones. Con 2 × 20 = 40 conexiones (db.t3.micro admite ~80) no hubo ningún 5xx. | Sección 9.1 |
| `bookings` en la analítica cuenta solo tramos CONFIRMED; `cancel_rate = cancellations / (bookings + cancellations)`. | Definición sin ambigüedad: una venta cancelada no es una venta. | Sección 7.4 |

### C.7 Sección 11.1 — pie de la figura 2 y diagrama

> Figura 2. Arquitectura desplegada en AWS Academy Learner Lab: EC2 t3.small (API en Docker) → RDS PostgreSQL db.t3.micro con las bases `airline_oltp` y `airline_olap` → AWS Glue (2 conexiones JDBC, 2 crawlers, 1 job) → Glue Data Catalog. S3 (script y evidencias) se accede por el endpoint Gateway; las credenciales están en SSM Parameter Store.

En el diagrama: cambiar la caja "Aurora PostgreSQL" por "RDS PostgreSQL 16 (db.t3.micro)", con las dos bases dentro.

Datos de la ejecución del ETL para la 11.2: job `airline-etl-oltp-to-olap`, Glue 4.0, 2 × G.1X, **110 s y 220 DPU-s**, estado SUCCEEDED. El catálogo tiene 10 tablas en `airline_oltp` y 2 en `airline_olap`. Tras retirar los vuelos de prueba, `fact_route_month` tiene 48 filas (8 rutas × 6 meses, junio a noviembre de 2026) y `fact_fare_month` 12 (2 clases × 6 meses).

### C.8 Sección 11.3 — reemplazar las dos primeras filas

| Servicio | Alternativa considerada | Requisito | Pilar Well-Architected |
|---|---|---|---|
| RDS PostgreSQL 16 (1 instancia db.t3.micro, cifrada) | Aurora PostgreSQL; DynamoDB | RF-02, RF-05, RNF-03 | Confiabilidad: bloqueo por fila y transacciones ACID. Costos: la talla mínima cumple RNF-01 y RNF-03 (p95 de 83 ms, cero sobreventas). Aurora costaría ~5 veces más sin beneficio medible. |
| Segunda base airline_olap en la misma instancia | Otra instancia RDS | RF-13, RF-14, RNF-09 | Costos: una segunda instancia duplicaría el costo. Eficiencia de rendimiento: modelo agregado para lectura. |

### C.9 Sección 11.4 — reemplazar la tabla de costos

Precios públicos aproximados en us-east-1. La columna de laboratorio supone unas 60 horas encendido al mes; el resto del tiempo RDS y EC2 se detienen con `apagar.sh` (el almacenamiento se sigue cobrando).

| Servicio | Supuesto | 24/7 al mes | Laboratorio (~60 h) |
|---|---|---|---|
| RDS db.t3.micro | US$0,017 por hora | US$12,4 | US$1,0 |
| Almacenamiento RDS | 20 GB gp3 a US$0,115 por GB | US$2,3 | US$2,3 |
| EC2 t3.small | US$0,0208 por hora | US$15,2 | US$1,2 |
| EBS de la EC2 | 8 GB gp3 a US$0,08 por GB | US$0,6 | US$0,6 |
| Job de Glue | 2 DPU × ~2 min (medido: 220 DPU-s) × 30 ejecuciones a US$0,44 por DPU-h | US$0,8 | US$0,2 |
| Crawlers de Glue | ~4 ejecuciones, mínimo 10 min | US$0,3 | US$0,3 |
| Data Catalog, S3, endpoint, SSM | Capas gratuitas, <1 GB | US$0,1 | US$0,1 |
| **Total** | | **~US$32** | **~US$6** |

---

## D. Anexo — Registro de prompts (borrador)

> Completen las filas 1 a 3 con los prompts reales que usaron para el diseño (secciones 1 a 8). Las demás corresponden a la implementación en AWS.

| # | Prompt (resumen) | Resultado del asistente | Corrección o refinamiento del equipo |
|---|---|---|---|
| 1 | *(Diseño inicial de requisitos y modelo E-R)* | 87 RF y 24 entidades | Se simplificó a 16 RF y 10 tablas por alcance y presupuesto (sección 7.5). |
| 2 | *(Mecanismo de concurrencia)* | *(completar)* | *(completar)* |
| 3 | *(Arquitectura AWS y costos)* | Aurora db.t3.medium | Se cambió a RDS db.t3.micro (fila 6). |
| 4 | "Conéctate a AWS con las credenciales de .aws; nada local, todo en la nube; haz un plan y asigna tareas a agentes" | Plan en 6 fases: código, infraestructura, API y pruebas, Glue, backup | Se aprobó el plan y se pidió que el backup incluyera GitHub. |
| 5 | Especificación común (SPEC.md) con tablas, endpoints y regla de concurrencia | El código y la infraestructura siguieron el SPEC | Se exigió que el puerto 5432 nunca se abriera a 0.0.0.0/0, por un incidente previo de ransomware en una base expuesta. |
| 6 | "Ten en cuenta el enunciado y el harness del parcial 2" | Se detectó que Aurora db.t3.medium incumple el límite de clases de la siguiente fase | Se recreó la base como RDS db.t3.micro y se adaptó el contrato de la API (`flight_id` texto, `passenger_name`). |
| 7 | Revisión del código generado | La API devolvía 503 al agotarse el pool | Se amplió el pool a 20 conexiones por worker y se verificó que no hubiera ningún 5xx en 50 peticiones concurrentes. |
| 8 | Revisión del ETL | `bookings` contaba tramos confirmados y cancelados | Se redefinió como solo confirmados y se ajustó `cancel_rate`. |
| 9 | "Limpia los vuelos de prueba y vuelve a correr el ETL" | Script transaccional que aborta si una reserva de prueba toca vuelos reales | Se borraron 21 vuelos y 35 reservas de prueba; tras el ETL quedaron 48 filas en fact_route_month (8 rutas × 6 meses) y 12 en fact_fare_month. |
