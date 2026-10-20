"""Job PySpark (EMR) de curación del histórico de aerolínea.

Lee los CSV crudos (flights.csv y reservations.csv), aplica las reglas de limpieza
C1-C5 (idénticas a airline_data/oracle.py) y escribe dos tablas en Parquet:
  1) reservations_curated : detalle limpio, particionado por month (YYYY-MM)
  2) route_month_metrics  : métricas por ruta y mes (un solo archivo)

Uso:
  spark-submit curate.py --input s3://bucket/raw/ --output s3://bucket/curated/
"""
import argparse

from pyspark.sql import SparkSession, functions as F


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="prefijo S3 con flights.csv y reservations.csv")
    ap.add_argument("--output", required=True, help="prefijo S3 de salida (curated/)")
    a = ap.parse_args()
    inp = a.input.rstrip("/") + "/"
    out = a.output.rstrip("/") + "/"

    spark = SparkSession.builder.appName("airline-curate").getOrCreate()

    # ---- Lectura: todo como string para controlar nosotros los casts
    # (price_cents puede venir vacío o con decimales; los ids no deben quedar como "123.0").
    flights = spark.read.option("header", True).option("inferSchema", False).csv(inp + "flights.csv")
    res = spark.read.option("header", True).option("inferSchema", False).csv(inp + "reservations.csv")

    # ---- C1: route -> quitar TODO espacio en blanco y pasar a mayúsculas
    flights = flights.withColumn("route", F.upper(F.regexp_replace(F.col("route"), r"\s+", "")))
    # month = 'YYYY-MM' de flight_date; capacity numérica
    flights = (flights
               .withColumn("month", F.substring(F.col("flight_date"), 1, 7))
               .withColumn("capacity", F.col("capacity").cast("double").cast("bigint"))
               .withColumn("flight_id", F.col("flight_id").cast("double").cast("bigint").cast("string"))
               .select("flight_id", "route", "month", "capacity"))

    # ---- C2: status -> trim + mayúsculas
    res = res.withColumn("status", F.upper(F.trim(F.col("status"))))
    # ---- C3: solo CONFIRMED o CANCELLED
    res = res.filter(F.col("status").isin("CONFIRMED", "CANCELLED"))
    # ---- C4: price_cents no nulo y > 0 (se lee como double por si viene "123.0" o vacío)
    res = res.withColumn("price_d", F.col("price_cents").cast("double"))
    res = res.filter(F.col("price_d").isNotNull() & (F.col("price_d") > 0))
    res = res.withColumn("price_cents", F.col("price_d").cast("bigint")).drop("price_d")
    # ids como string sin ".0"
    res = (res
           .withColumn("reservation_id", F.col("reservation_id").cast("double").cast("bigint").cast("string"))
           .withColumn("flight_id", F.col("flight_id").cast("double").cast("bigint").cast("string")))
    # ---- C5: quitar reservation_id duplicados (las repetidas son copias exactas)
    res = res.dropDuplicates(["reservation_id"])

    # ---- Join inner con vuelos limpios (aporta route y month)
    detail = (res.join(flights.select("flight_id", "route", "month"), on="flight_id", how="inner")
                 .select("reservation_id", "flight_id", "route", "fare_class", "price_cents", "status", "month"))

    # ---- Salida 1: detalle curado, Parquet particionado por month.
    # repartition("month") => un archivo (o muy pocos) por partición.
    (detail.repartition("month")
           .write.mode("overwrite").partitionBy("month").parquet(out + "reservations_curated/"))

    # ---- Salida 2: métricas por ruta y mes
    # Capacidad = suma de capacity de los vuelos de la ruta en el mes (vuelos sin reservas también cuentan).
    cap = flights.groupBy("route", "month").agg(F.sum("capacity").alias("seats_capacity"))
    det = detail.groupBy("route", "month").agg(
        F.sum(F.when(F.col("status") == "CONFIRMED", 1).otherwise(0)).alias("seats_sold"),
        F.sum(F.when(F.col("status") == "CONFIRMED", F.col("price_cents")).otherwise(0)).alias("revenue_cents"),
        F.count(F.lit(1)).alias("bookings"),  # CONFIRMED + CANCELLED, igual que el oráculo
        F.sum(F.when(F.col("status") == "CANCELLED", 1).otherwise(0)).alias("cancellations"),
    )
    m = cap.join(det, on=["route", "month"], how="left").na.fill(0)
    m = m.select(
        "route", "month",
        F.col("seats_capacity").cast("bigint").alias("seats_capacity"),
        F.col("seats_sold").cast("bigint").alias("seats_sold"),
        (F.col("seats_sold") / F.col("seats_capacity")).cast("double").alias("occupancy"),
        F.col("revenue_cents").cast("bigint").alias("revenue_cents"),
        F.col("bookings").cast("bigint").alias("bookings"),
        F.col("cancellations").cast("bigint").alias("cancellations"),
    )
    m.coalesce(1).write.mode("overwrite").parquet(out + "route_month_metrics/")

    spark.stop()


if __name__ == "__main__":
    main()
