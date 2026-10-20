-- Borra los vuelos creados por pytest/harness (sin vuelo programado o con fecha >= 2027)
-- y todo lo que cuelga de ellos. Los datos del seed (AVxxx-2026xxxx) no se tocan.
BEGIN;

CREATE TEMP TABLE test_flights AS
SELECT flight_id FROM flights
WHERE flight_number IS NULL OR flight_date >= DATE '2027-01-01';

CREATE TEMP TABLE test_reservations AS
SELECT DISTINCT reservation_id FROM reservation_segments
WHERE flight_id IN (SELECT flight_id FROM test_flights);

CREATE TEMP TABLE test_passengers AS
SELECT DISTINCT passenger_id FROM reservations
WHERE reservation_id IN (SELECT reservation_id FROM test_reservations);

SELECT (SELECT count(*) FROM test_flights)      AS vuelos_prueba,
       (SELECT count(*) FROM test_reservations) AS reservas_prueba,
       (SELECT count(*) FROM test_passengers)   AS pasajeros_prueba;

-- Un itinerario de prueba no puede tocar vuelos del seed: si pasa, se aborta.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM reservation_segments
             WHERE reservation_id IN (SELECT reservation_id FROM test_reservations)
               AND flight_id NOT IN (SELECT flight_id FROM test_flights)) THEN
    RAISE EXCEPTION 'una reserva de prueba usa vuelos del seed; revisar a mano';
  END IF;
END $$;

DELETE FROM payments             WHERE reservation_id IN (SELECT reservation_id FROM test_reservations);
DELETE FROM reservation_segments WHERE reservation_id IN (SELECT reservation_id FROM test_reservations);
DELETE FROM reservations         WHERE reservation_id IN (SELECT reservation_id FROM test_reservations);
DELETE FROM passengers p
WHERE passenger_id IN (SELECT passenger_id FROM test_passengers)
  AND NOT EXISTS (SELECT 1 FROM reservations r WHERE r.passenger_id = p.passenger_id);
DELETE FROM flights              WHERE flight_id IN (SELECT flight_id FROM test_flights);
-- Rutas creadas por /admin/flights que quedaron sin vuelos ni vuelo programado
DELETE FROM routes r
WHERE NOT EXISTS (SELECT 1 FROM flights f WHERE f.route_code = r.route_code)
  AND NOT EXISTS (SELECT 1 FROM scheduled_flights s WHERE s.route_code = r.route_code);

SELECT (SELECT count(*) FROM flights)      AS vuelos,
       (SELECT count(*) FROM reservations) AS reservas,
       (SELECT count(*) FROM routes)       AS rutas,
       (SELECT count(*) FROM flights f
         WHERE f.seats_available <> f.capacity - (
           SELECT count(*) FROM reservation_segments s JOIN reservations r USING (reservation_id)
           WHERE s.flight_id = f.flight_id AND r.status = 'CONFIRMED')) AS vuelos_inconsistentes;

COMMIT;
