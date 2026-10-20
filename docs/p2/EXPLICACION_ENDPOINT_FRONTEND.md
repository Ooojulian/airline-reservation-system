# Endpoint analítico y frontend (Parcial 2)

## Qué se construyó
- `GET /analytics/occupancy?route=&month=` en la misma API FastAPI (`backend/app/analytics.py`).
- Frontend estático (HTML + JS sin build) en nginx, contenedor `airline-web`, puerto 3000, misma EC2.
  `/` = cliente (buscar, reservar, confirmación); `/manager` = panel gerencial (ocupación por ruta y mes).

## Cómo obtiene el panel los números
Navegador -> nginx (`/api/analytics/occupancy`) -> API -> Athena -> tabla `route_month_metrics`
(Parquet en S3, generada por el job Spark en EMR). El panel solo formatea `occupancy*100` con 3 decimales
(ej. `78.432%`), así que muestra exactamente lo que devuelve el endpoint, que a su vez sale de Athena.
- Consulta parametrizada: `WHERE route = ? AND month = ?` con `ExecutionParameters`. Además route y month
  se validan con regex (`^[A-Z]{3}-[A-Z]{3}$`, `^\d{4}-(0[1-9]|1[0-2])$`) antes de tocar Athena: 422 si no cumplen.
  Nunca se concatena input al SQL (sin inyección).
- Polling del estado de la consulta con backoff 0.2 s -> 1 s y timeout de 9 s (el requisito es respuesta <= 10 s).
- 200 con la fila; 404 si no hay fila; 503 solo si falla Athena (tabla inexistente, permisos, timeout).
- Credenciales: rol de la EC2 (LabInstanceProfile) vía IMDS. El contenedor las obtiene porque el hop limit de
  IMDSv2 de la instancia ya es 2 (verificado con `boto3.Session().get_credentials().method == "iam-role"` dentro
  del contenedor). No hay claves en el código ni en variables de entorno.

## Latencia, costo y frescura
- Latencia: una consulta Athena puntual sobre una tabla Parquet pequeña tarda ~1-3 s (medido: ~1.4 s hasta
  fallar por esquema inexistente; con tabla real se espera 1-3 s). Con caché, ms.
- Costo: Athena cobra US$5 por TB escaneado, mínimo 10 MB por consulta -> ~US$0.00005 por consulta. La tabla
  de métricas (rutas x 24 meses) pesa KB, así que cada consulta cuesta el mínimo. S3 de resultados: centavos.
- Frescura: los datos son tan frescos como la última corrida de EMR (procesamiento batch). El caché añade como
  máximo 10 min de desfase adicional (TTL), irrelevante frente al batch.

## Por qué caché solo tras la primera consulta
- El harness exige comprobar que el dato viene de Athena: la primera llamada para cada (route, month) siempre
  consulta Athena; no se precarga nada ni se hardcodea.
- Después, repetir el mismo par no tiene sentido pagar otra consulta ni 1-3 s: el dato solo cambia cuando EMR
  vuelve a correr. Solo se cachean consultas exitosas (nunca 404 ni errores), con TTL de 10 min. El caché es por
  proceso (2 workers), suficiente para este uso; en producción sería Redis o invalidación al terminar el job.

## Por qué frontend estático en la misma EC2
- Costo: nginx:alpine consume ~10 MB; no se paga S3+CloudFront, otra instancia ni balanceador. Presupuesto total US$50.
- R11 (costo): cero servicios facturables nuevos para el front.
- Sin build ni framework: HTML+JS puro, nada que compilar ni mantener.
- nginx hace proxy `/api/` -> `127.0.0.1:8000` (contenedor con `--network host`), así el navegador habla con un solo
  origen: sin problemas de CORS ni de IP cambiante de la EC2. Aun así la API tiene CORS permisivo (GET/POST).
- Puerto 3000 abierto solo a las IPs del equipo en el Security Group.

## Separación de cargas (R1/R2)
Las reservas y búsquedas (OLTP) van a RDS PostgreSQL; el panel gerencial (OLAP) va a Athena sobre S3. Ninguna
consulta analítica toca RDS, así que un directivo consultando ocupaciones no consume conexiones ni CPU de la base
transaccional: la latencia de búsqueda (R1) y la concurrencia sin sobreventa (R2) no se ven afectadas. Un fallo de
Athena solo genera 503 en `/analytics/occupancy`; el resto de la API sigue funcionando.

## Preguntas probables de sustentación
- **¿De dónde sale el número del panel?** De Athena, tabla `route_month_metrics` (Parquet de EMR), vía el endpoint.
- **¿Por qué no consultar RDS?** Mezclaría carga analítica con la transaccional y arriesga R1/R2; además el
  requisito es explotar el data lake.
- **¿Cómo evitan SQL injection?** Regex estricta + `ExecutionParameters` de Athena (consulta parametrizada).
- **¿Qué pasa si Athena tarda o falla?** Timeout de 9 s -> se cancela la consulta y se responde 503 claro; el resto
  de la API no se afecta.
- **¿No es trampa el caché?** No: la primera consulta de cada par siempre va a Athena; el caché es TTL de 10 min
  y los datos solo cambian al correr EMR.
- **¿Cuánto cuesta cada consulta?** ~US$0.00005 (mínimo de 10 MB escaneados).
- **¿Cómo obtiene credenciales el contenedor?** Rol de la instancia por IMDS (hop limit 2); sin claves.
- **¿Por qué 3 decimales en el %?** Para que el valor mostrado coincida con el endpoint dentro de 0.001.
- **¿Por qué nginx y no servir desde FastAPI o S3?** Aislar estático del API, proxy a un solo origen, costo cero extra.
- **¿Qué se haría en producción?** Elastic IP + HTTPS (ALB o CloudFront), caché compartido, tabla materializada
  actualizada por orquestación (Step Functions/Airflow) y autenticación en el panel.
