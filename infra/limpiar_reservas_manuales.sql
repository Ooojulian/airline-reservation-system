-- Borra reservas de prueba hechas a mano/harness sobre vuelos del seed (pasajero no 'Pasajero N', creadas 2026-10-21/22)
-- y restaura seats_available. Mismo cascada que limpiar_vuelos_prueba.sql.
BEGIN;
CREATE TEMP TABLE del_res AS
SELECT DISTINCT r.reservation_id, r.passenger_id, r.status FROM reservations r
JOIN passengers p USING (passenger_id)
JOIN reservation_segments s USING (reservation_id)
JOIN flights f USING (flight_id)
WHERE p.full_name NOT LIKE 'Pasajero %' AND f.flight_number IS NOT NULL AND f.flight_date < DATE '2027-01-01'
  AND r.created_at >= TIMESTAMPTZ '2026-10-21 00:00+00' AND r.created_at < TIMESTAMPTZ '2026-10-23 12:00+00';
SELECT * FROM del_res;
UPDATE flights f SET seats_available = seats_available + c.n
FROM (SELECT s.flight_id, count(*) n FROM reservation_segments s JOIN del_res d USING (reservation_id)
      WHERE d.status = 'CONFIRMED' GROUP BY 1) c WHERE c.flight_id = f.flight_id;
DELETE FROM seat_holds          WHERE reservation_id IN (SELECT reservation_id FROM del_res);
DELETE FROM seat_assignments    WHERE reservation_id IN (SELECT reservation_id FROM del_res);
DELETE FROM payments            WHERE reservation_id IN (SELECT reservation_id FROM del_res);
DELETE FROM reservation_segments WHERE reservation_id IN (SELECT reservation_id FROM del_res);
DELETE FROM reservations        WHERE reservation_id IN (SELECT reservation_id FROM del_res);
DELETE FROM passengers p WHERE passenger_id IN (SELECT passenger_id FROM del_res)
  AND NOT EXISTS (SELECT 1 FROM reservations r WHERE r.passenger_id = p.passenger_id);
COMMIT;
