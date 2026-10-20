"""Glue 4.0 (PySpark): ETL airline_oltp -> airline_olap.

Lee flights, reservations, reservation_segments y fare_rules por JDBC, agrega por
ruta x mes y clase x mes (conteo por tramo; revenue solo de CONFIRMED; sin datos personales)
y reemplaza las tablas fact_* del OLAP (TRUNCATE + append). flight_id es texto.
Mes = primer día del mes de flight_date.
"""
import re
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F

args = getResolvedOptions(sys.argv, ["JOB_NAME"])
sc = SparkContext()
glue = GlueContext(sc)
spark = glue.spark_session
job = Job(glue)
job.init(args["JOB_NAME"], args)


def jdbc_opts(connection_name: str, dbname: str) -> dict:
    """Credenciales de la conexión Glue; el URL se reconstruye para apuntar a la base indicada."""
    conf = glue.extract_jdbc_conf(connection_name)
    host_part = re.match(r"^(jdbc:postgresql://[^/?]+)", conf["url"]).group(1)
    return {
        "url": f"{host_part}/{dbname}?sslmode=require",
        "user": conf["user"],
        "password": conf["password"],
        "driver": "org.postgresql.Driver",
    }


oltp = jdbc_opts("airline-oltp-conn", "airline_oltp")
olap = jdbc_opts("airline-olap-conn", "airline_olap")


def read(table: str):
    return spark.read.format("jdbc").options(**oltp).option("dbtable", table).load()


flights = read("flights").select("flight_id", "route_code", "flight_date", "capacity")
reservations = read("reservations").select("reservation_id", "status", "fare_rule_id")
segments = read("reservation_segments").select("reservation_id", "flight_id", "price_cents")
fare_rules = read("fare_rules").select("fare_rule_id", "fare_class")

flights = flights.withColumn("month", F.trunc("flight_date", "month"))

# Un renglón por tramo con su ruta, mes, clase tarifaria y estado
seg = (
    segments.join(reservations, "reservation_id")
    .join(fare_rules, "fare_rule_id")
    .join(flights.select("flight_id", "route_code", "month"), "flight_id")
    .withColumn("confirmed", (F.col("status") == "CONFIRMED").cast("int"))
    .withColumn("cancelled", (F.col("status") == "CANCELLED").cast("int"))
    .withColumn("rev", F.when(F.col("status") == "CONFIRMED", F.col("price_cents")).otherwise(F.lit(0)))
)

# ---- fact_route_month ----
cap = flights.groupBy("route_code", "month").agg(F.sum("capacity").cast("long").alias("seats_capacity"))
sold = seg.groupBy("route_code", "month").agg(
    F.sum("confirmed").cast("long").alias("seats_sold"),
    F.sum("rev").cast("long").alias("revenue_cents"),
    F.sum("confirmed").cast("long").alias("bookings"),
    F.sum("cancelled").cast("long").alias("cancellations"),
)
fact_route = (
    cap.join(sold, ["route_code", "month"], "left")
    .na.fill(0, ["seats_sold", "revenue_cents", "bookings", "cancellations"])
    .withColumn("occupancy", F.round(F.col("seats_sold") / F.col("seats_capacity"), 4).cast("decimal(7,4)"))
    .withColumn("etl_loaded_at", F.current_timestamp())
    .select("route_code", "month", "seats_capacity", "seats_sold", "occupancy",
            "revenue_cents", "bookings", "cancellations", "etl_loaded_at")
)

# ---- fact_fare_month ----
fact_fare = (
    seg.groupBy("fare_class", "month")
    .agg(
        F.sum("rev").cast("long").alias("revenue_cents"),
        F.sum("confirmed").cast("long").alias("bookings"),
        F.sum("cancelled").cast("long").alias("cancellations"),
    )
    .withColumn("cancel_rate", F.round(F.col("cancellations") / (F.col("bookings") + F.col("cancellations")), 4).cast("decimal(7,4)"))
    .withColumn("etl_loaded_at", F.current_timestamp())
    .select("fare_class", "month", "revenue_cents", "bookings", "cancellations", "cancel_rate", "etl_loaded_at")
)


def write(df, table: str):
    # overwrite + truncate=true => TRUNCATE de la tabla existente (conserva DDL y PK) y luego INSERT
    (df.write.format("jdbc").options(**olap).option("dbtable", table)
       .option("truncate", "true").mode("overwrite").save())


write(fact_route, "fact_route_month")
write(fact_fare, "fact_fare_month")
print(f"fact_route_month: {fact_route.count()} filas; fact_fare_month: {fact_fare.count()} filas")

job.commit()
