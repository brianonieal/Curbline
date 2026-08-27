-- Validation fixture. Real NYC coordinates, chosen to exercise the three
-- cases that matter: a tight cluster, a distant isolated sensor, and a
-- two-sensor cluster that would degenerate to a LINESTRING without buffering.

-- Cluster 1: four sensors in southeast Queens (Ida flooding corridor),
-- all within a few hundred metres of each other.
INSERT INTO sensors (sensor_id, name, geom) VALUES
 ('q1', 'Jamaica Ave & 150th',   ST_SetSRID(ST_MakePoint(-73.7975, 40.7020), 4326)),
 ('q2', 'Jamaica Ave & 153rd',   ST_SetSRID(ST_MakePoint(-73.7940, 40.7025), 4326)),
 ('q3', 'Hillside Ave & 150th',  ST_SetSRID(ST_MakePoint(-73.7968, 40.7062), 4326)),
 ('q4', '90th Ave & 150th',      ST_SetSRID(ST_MakePoint(-73.7981, 40.6995), 4326)),
-- Cluster 2: exactly two sensors in Red Hook. Convex hull of 2 points is a
-- LINESTRING, so this is the degenerate case the buffer exists to handle.
 ('b1', 'Van Brunt & Beard',     ST_SetSRID(ST_MakePoint(-74.0165, 40.6748), 4326)),
 ('b2', 'Van Brunt & Reed',      ST_SetSRID(ST_MakePoint(-74.0152, 40.6761), 4326)),
-- Noise: one wet sensor alone in the Bronx, far from everything.
 ('x1', 'Bruckner & Zerega',     ST_SetSRID(ST_MakePoint(-73.8430, 40.8290), 4326)),
-- Dry sensor inside cluster 1's footprint. Must NOT be pulled into the zone.
 ('q5', 'Jamaica Ave & 151st',   ST_SetSRID(ST_MakePoint(-73.7960, 40.7018), 4326));

INSERT INTO readings (ingest_id, sensor_id, observed_at, depth_cm, source) VALUES
 (gen_random_uuid(), 'q1', now() - interval '2 min', 14.5, 'fixture'),
 (gen_random_uuid(), 'q2', now() - interval '2 min',  9.8, 'fixture'),
 (gen_random_uuid(), 'q3', now() - interval '1 min', 22.1, 'fixture'),
 (gen_random_uuid(), 'q4', now() - interval '3 min',  7.2, 'fixture'),
 (gen_random_uuid(), 'b1', now() - interval '1 min', 11.0, 'fixture'),
 (gen_random_uuid(), 'b2', now() - interval '2 min', 13.4, 'fixture'),
 (gen_random_uuid(), 'x1', now() - interval '1 min', 18.0, 'fixture'),
 (gen_random_uuid(), 'q5', now() - interval '1 min',  0.4, 'fixture');

-- A flash flood warning polygon covering southeast Queens but NOT Red Hook.
-- Cluster 1 should correlate; cluster 2 should not.
INSERT INTO alerts (alert_id, event, severity, headline, effective, expires, geom)
VALUES (
  'urn:oid:test.ffw.1', 'Flash Flood Warning', 'Severe',
  'Flash Flood Warning for southeast Queens',
  now() - interval '30 min', now() + interval '2 hours',
  ST_Multi(ST_SetSRID(ST_GeomFromText(
    'POLYGON((-73.82 40.69, -73.77 40.69, -73.77 40.72, -73.82 40.72, -73.82 40.69))'
  ), 4326))
);
