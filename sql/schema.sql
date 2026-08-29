-- Curbline schema. Run once against the RDS instance, from the EC2 host.
--
-- Projection note: all geometry is stored in EPSG:4326 (lon/lat, degrees)
-- because that is what both FloodNet and the NWS emit and what MapLibre
-- consumes. Every DISTANCE operation transforms to EPSG:2263, which is
-- NAD83 / New York Long Island in US survey feet. Clustering in raw degrees
-- would make the epsilon mean different distances at different latitudes.

CREATE EXTENSION IF NOT EXISTS postgis;

-- Screenshot this for the report. It proves the extension is live on a
-- managed RDS instance rather than a self-installed database.
SELECT PostGIS_Full_Version();


-- ---------------------------------------------------------------------------
-- Reference data. Slow-changing, read on every message, so this is the
-- read-through cache target rather than an arbitrary choice of one.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sensors (
    sensor_id     TEXT PRIMARY KEY,
    name          TEXT,
    geom          geometry(Point, 4326) NOT NULL,
    deployed_on   DATE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sensors_geom_gix ON sensors USING GIST (geom);


-- ---------------------------------------------------------------------------
-- Raw observations. ingest_id is the idempotency key: the correlator claims
-- a unit by inserting it, and a duplicate delivery is a no-op rather than an
-- error. SQS is at-least-once, so this is not optional.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS readings (
    reading_id    BIGSERIAL PRIMARY KEY,
    ingest_id     UUID UNIQUE NOT NULL,
    sensor_id     TEXT NOT NULL REFERENCES sensors(sensor_id),
    observed_at   TIMESTAMPTZ NOT NULL,
    depth_cm      NUMERIC(6,2) NOT NULL,
    source        TEXT NOT NULL,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS readings_recent_ix
    ON readings (observed_at DESC, sensor_id);


-- ---------------------------------------------------------------------------
-- NWS active alerts. Geometry is nullable on purpose: zone-based products
-- return no polygon and reference UGC zones instead. A NULL here is a real
-- state, not a bug, and correlation must tolerate it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
    alert_id      TEXT PRIMARY KEY,
    event         TEXT NOT NULL,
    severity      TEXT,
    headline      TEXT,
    effective     TIMESTAMPTZ,
    expires       TIMESTAMPTZ,
    geom          geometry(MultiPolygon, 4326),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alerts_geom_gix ON alerts USING GIST (geom);
CREATE INDEX IF NOT EXISTS alerts_expires_ix ON alerts (expires);


-- ---------------------------------------------------------------------------
-- The product. A zone is several adjacent sensors inundated at once, which is
-- the thing a dispatcher can act on. A single sensor reading is not.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS zones (
    zone_id       UUID PRIMARY KEY,
    hull          geometry(Polygon, 4326) NOT NULL,
    sensor_ids    TEXT[] NOT NULL,
    sensor_count  INT NOT NULL,
    max_depth_cm  NUMERIC(6,2) NOT NULL,
    state         TEXT NOT NULL
                  CHECK (state IN ('forming','active','receding','closed')),
    under_alert   BOOLEAN NOT NULL DEFAULT FALSE,
    alert_id      TEXT REFERENCES alerts(alert_id),
    opened_at     TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL,
    closed_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS zones_hull_gix ON zones USING GIST (hull);
CREATE INDEX IF NOT EXISTS zones_open_ix ON zones (state)
    WHERE state <> 'closed';


CREATE TABLE IF NOT EXISTS advisories (
    advisory_id    UUID PRIMARY KEY,
    zone_id        UUID NOT NULL REFERENCES zones(zone_id),
    level          TEXT NOT NULL,
    message        TEXT NOT NULL,
    issued_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sns_message_id TEXT,
    audit_key      TEXT
);
CREATE INDEX IF NOT EXISTS advisories_zone_ix ON advisories (zone_id, issued_at DESC);


-- ---------------------------------------------------------------------------
-- Latest reading per sensor. DISTINCT ON is the Postgres idiom for this and
-- is far cheaper than a window function over the whole table.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW latest_readings AS
SELECT DISTINCT ON (r.sensor_id)
       r.sensor_id,
       r.depth_cm,
       r.observed_at,
       s.name,
       s.geom
FROM readings r
JOIN sensors s USING (sensor_id)
ORDER BY r.sensor_id, r.observed_at DESC;


-- ---------------------------------------------------------------------------
-- The clustering function. This is the core of the application and the reason
-- the database is PostGIS rather than a key-value store.
--
--   p_threshold_cm : depth at which a sensor counts as inundated
--   p_window_mins  : how recent a reading must be to count as current
--   p_eps_ft       : cluster radius, in feet (1640 ft is about 500 m)
--   p_minpoints    : sensors required to form a zone. 2 is deliberate:
--                    one wet sensor is a reading, two adjacent wet sensors
--                    are a flooding street.
-- ---------------------------------------------------------------------------
-- WARNING on the defaults below. They are FloodNet's calibration. The workers
-- never use them, db.py passes all four explicitly from config, but a hand-run
-- `SELECT * FROM current_clusters();` gets 5.0 cm regardless of what
-- CURBLINE_SOURCE is set to. On a usgs stack, where detection starts at 60,
-- that returns clusters the running system would never form. Pass the four
-- arguments explicitly when capturing evidence, and say which source they came
-- from. See E-014 and E-025.
CREATE OR REPLACE FUNCTION current_clusters(
    p_threshold_cm NUMERIC DEFAULT 5.0,
    p_window_mins  INT     DEFAULT 15,
    p_eps_ft       NUMERIC DEFAULT 1640,
    p_minpoints    INT     DEFAULT 2
)
RETURNS TABLE (
    cluster_id   INT,
    sensor_ids   TEXT[],
    sensor_count INT,
    max_depth_cm NUMERIC,
    hull         geometry(Polygon, 4326)
)
LANGUAGE sql STABLE AS $$
    WITH breaching AS (
        SELECT sensor_id, depth_cm, geom
        FROM latest_readings
        WHERE depth_cm >= p_threshold_cm
          AND observed_at > now() - (p_window_mins || ' minutes')::interval
    ),
    clustered AS (
        SELECT
            ST_ClusterDBSCAN(
                ST_Transform(geom, 2263),
                eps       := p_eps_ft,
                minpoints := p_minpoints
            ) OVER () AS cid,
            sensor_id, depth_cm, geom
        FROM breaching
    )
    SELECT
        cid,
        array_agg(sensor_id ORDER BY sensor_id),
        COUNT(*)::INT,
        MAX(depth_cm),
        -- A 2-point cluster convex-hulls to a LINESTRING and a 1-point cluster
        -- to a POINT. Both violate geometry(Polygon) and render as nothing on
        -- a map. Buffering by 150 m in the projected CRS forces a polygon and
        -- gives the zone a sensible visual footprint.
        ST_Transform(
            ST_Buffer(
                ST_ConvexHull(ST_Collect(ST_Transform(geom, 2263))),
                492            -- 492 ft, about 150 m
            ),
            4326
        )::geometry(Polygon, 4326)
    FROM clustered
    WHERE cid IS NOT NULL      -- DBSCAN marks noise points NULL; those are
    GROUP BY cid;              -- isolated wet sensors, not zones.
$$;


-- ---------------------------------------------------------------------------
-- Alert correlation. LEFT JOIN, not INNER: a zone with no matching warning is
-- still a zone. It just carries less confirmation.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION alert_for_hull(p_hull geometry)
RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT a.alert_id
    FROM alerts a
    WHERE a.geom IS NOT NULL
      -- The endpoint is /alerts/active, so anything stored here was active
      -- when it was fetched. `expires > now()` is false for NULL, which meant
      -- an alert with no expiry field was silently never correlated, never
      -- drawn and never counted. NULL is not fabricated into a timestamp:
      -- instead it means "active while the feed still lists it", bounded by
      -- updated_at, which the upsert refreshes on every poll. See E-029.
      AND (a.expires > now()
           OR (a.expires IS NULL
               AND a.updated_at > now() - interval '30 minutes'))
      AND ST_Intersects(p_hull, a.geom)
    ORDER BY
        CASE a.severity
            WHEN 'Extreme'  THEN 1
            WHEN 'Severe'   THEN 2
            WHEN 'Moderate' THEN 3
            ELSE 4
        END
    LIMIT 1;
$$;
