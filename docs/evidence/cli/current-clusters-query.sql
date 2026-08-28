-- Verification query for the spatial core, run against the live RDS PostGIS
-- instance on 2026-08-28 while the replay pipeline was active.
--
-- ST_GeometryType(hull) is the point of it. D-006 and E-002 record that a
-- two-sensor cluster convex-hulls to a LINESTRING and a one-sensor cluster to a
-- POINT, both of which violate geometry(Polygon, 4326) and render invisibly.
-- current_clusters() buffers by 492 ft to force a polygon, so every row this
-- returns must report ST_Polygon. Anything else means the buffer regressed.
SELECT cluster_id,
       sensor_ids,
       sensor_count,
       max_depth_cm,
       ST_GeometryType(hull)
FROM current_clusters();
