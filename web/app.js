/* ==========================================================================
   Curbline console

   One WebSocket carries the whole application state on every tick. The client
   holds no derived state of its own beyond what is needed to animate between
   ticks, which keeps the browser from ever disagreeing with the database.
   ========================================================================== */

'use strict';

const RAIL_MAX_CM = 45;          // top of the gauge
const RAIL_GRAD_CM = 5;          // graduation interval
const CM_PER_INCH = 2.54;

// Depth ramp, shallow to deep. Matches the CSS custom properties so the map
// and the rail encode depth identically.
const WATER = ['#4E6B72', '#4C8C86', '#2E7F7C', '#1C6E74'];
const SODIUM = '#E5A33C';
const FLARE  = '#DD5530';

let map = null;
let mapReady = false;
let latest = null;
let seenAdvisories = new Set();
let firstPaint = true;

const $ = (id) => document.getElementById(id);
const cmToIn = (cm) => cm / CM_PER_INCH;

function depthColor(cm) {
  if (cm >= 20) return WATER[3];
  if (cm >= 12) return WATER[2];
  if (cm >= 6)  return WATER[1];
  return WATER[0];
}

function relativeTime(iso) {
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60)   return `${Math.floor(secs)}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

/* --------------------------------------------------------------------------
   Map
   -------------------------------------------------------------------------- */

function initMap() {
  map = new maplibregl.Map({
    container: 'map',
    // CARTO dark basemap: free, no API token, OSM-derived.
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    center: [-73.94, 40.70],
    zoom: 10.4,
    attributionControl: { compact: true },
  });

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }),
                 'top-right');

  map.on('load', () => {
    const empty = { type: 'FeatureCollection', features: [] };

    // Draw order matters and encodes precedence: weather context sits
    // underneath the zones it corroborates, and individual sensors sit on top
    // of everything because they are the evidence.
    map.addSource('alerts', { type: 'geojson', data: empty });
    map.addLayer({
      id: 'alert-fill', type: 'fill', source: 'alerts',
      paint: { 'fill-color': SODIUM, 'fill-opacity': 0.07 },
    });
    map.addLayer({
      id: 'alert-line', type: 'line', source: 'alerts',
      paint: {
        'line-color': SODIUM, 'line-width': 1,
        'line-opacity': 0.5, 'line-dasharray': [3, 2],
      },
    });

    map.addSource('zones', { type: 'geojson', data: empty });
    map.addLayer({
      id: 'zone-fill', type: 'fill', source: 'zones',
      paint: {
        // Placeholder stops. applyThresholds() overwrites these from
        // state.thresholds on the first frame; they are only what the layer
        // holds before any state has arrived. Do not read them as calibration.
        'fill-color': [
          'interpolate', ['linear'], ['get', 'max_depth_cm'],
          5, WATER[0], 12, WATER[1], 20, WATER[2], 30, WATER[3],
        ],
        'fill-opacity': [
          'case', ['==', ['get', 'state'], 'forming'], 0.2, 0.42,
        ],
      },
    });
    map.addLayer({
      id: 'zone-line', type: 'line', source: 'zones',
      paint: {
        'line-color': [
          'case', ['get', 'under_alert'], SODIUM, '#7FB3AE',
        ],
        'line-width': ['case', ['get', 'under_alert'], 2, 1.2],
        'line-dasharray': [
          'case', ['==', ['get', 'state'], 'forming'],
          ['literal', [2, 2]], ['literal', [1, 0]],
        ],
      },
    });

    map.addSource('sensors', { type: 'geojson', data: empty });
    // Dry sensors read as hollow marks: present, reporting, not alarming.
    map.addLayer({
      id: 'sensor-dry', type: 'circle', source: 'sensors',
      filter: ['any',
        ['==', ['get', 'depth_cm'], null],
        ['<', ['get', 'depth_cm'], 5]],
      paint: {
        'circle-radius': 2.6,
        'circle-color': 'transparent',
        'circle-stroke-width': 1,
        'circle-stroke-color': '#43525F',
      },
    });
    map.addLayer({
      id: 'sensor-wet', type: 'circle', source: 'sensors',
      filter: ['all',
        ['!=', ['get', 'depth_cm'], null],
        ['>=', ['get', 'depth_cm'], 5]],
      paint: {
        'circle-radius': [
          'interpolate', ['linear'], ['get', 'depth_cm'],
          5, 4, 20, 8.5, 40, 13,
        ],
        // Placeholder stops, overwritten by applyThresholds(). See above.
        'circle-color': [
          'interpolate', ['linear'], ['get', 'depth_cm'],
          5, WATER[0], 12, WATER[1], 20, WATER[2], 30, WATER[3],
        ],
        'circle-opacity': 0.9,
        'circle-stroke-width': 1,
        'circle-stroke-color': '#0E141A',
      },
    });

    mapReady = true;
    if (latest) paintMap(latest);

    const popup = new maplibregl.Popup({
      closeButton: false, offset: 12, maxWidth: '260px',
    });

    for (const layer of ['sensor-wet', 'sensor-dry']) {
      map.on('mouseenter', layer, (e) => {
        map.getCanvas().style.cursor = 'pointer';
        const p = e.features[0].properties;
        const cm = p.depth_cm == null ? null : Number(p.depth_cm);
        popup.setLngLat(e.lngLat).setHTML(`
          <div class="pop-name">${p.name || p.sensor_id}</div>
          <div class="pop-depth mono">${
            cm == null ? 'no reading'
                       : `${cm.toFixed(1)} cm <small>/ ${cmToIn(cm).toFixed(1)} in</small>`
          }</div>
          <div class="pop-meta mono">${p.sensor_id}</div>
          <div class="pop-meta">${
            p.observed_at ? relativeTime(p.observed_at) : 'never reported'
          }</div>
        `).addTo(map);
      });
      map.on('mouseleave', layer, () => {
        map.getCanvas().style.cursor = '';
        popup.remove();
      });
    }
  });
}

function paintMap(state) {
  if (!mapReady) return;
  map.getSource('alerts').setData(state.alerts);
  map.getSource('zones').setData(state.zones);
  map.getSource('sensors').setData(state.sensors);

  // Frame the event once, on the first tick that has one. After that the
  // operator's own pan and zoom is never overridden.
  if (firstPaint && state.zones.features.length) {
    const b = new maplibregl.LngLatBounds();
    for (const f of state.zones.features) {
      for (const ring of f.geometry.coordinates) {
        for (const c of ring) b.extend(c);
      }
    }
    map.fitBounds(b, { padding: 110, maxZoom: 14, duration: 900 });
    firstPaint = false;
  }
}

/* --------------------------------------------------------------------------
   Depth rail
   -------------------------------------------------------------------------- */

function buildRailScale(thresholds) {
  const scale = $('rail-scale');
  scale.innerHTML = '';

  for (let cm = 0; cm <= RAIL_MAX_CM; cm += RAIL_GRAD_CM) {
    const pct = (cm / RAIL_MAX_CM) * 100;
    const major = cm % 10 === 0;
    const g = document.createElement('div');
    g.className = `grad${major ? ' major' : ''}`;
    g.style.bottom = `${pct}%`;
    if (major) {
      g.innerHTML =
        `<span class="grad-num mono">${cm}</span>` +
        `<span class="grad-alt mono">${cmToIn(cm).toFixed(0)}"</span>`;
    }
    scale.appendChild(g);
  }

  // Reference lines. Curb height is a real-world landmark rather than a
  // system threshold, which is why it is labelled differently.
  const marks = [
    { cm: thresholds.advisory_cm, cls: 'advisory', tag: 'Advisory' },
    { cm: thresholds.curb_cm,     cls: 'curb',     tag: 'Curb' },
    { cm: thresholds.warning_cm,  cls: 'warning',  tag: 'Warning' },
  ];
  for (const m of marks) {
    const t = document.createElement('div');
    t.className = `thresh ${m.cls}`;
    t.style.bottom = `${(m.cm / RAIL_MAX_CM) * 100}%`;
    t.innerHTML = `<span class="thresh-tag">${m.tag}</span>`;
    scale.appendChild(t);
  }
}

function paintRail(state) {
  const scale = $('rail-scale');
  if (!scale.dataset.built) {
    buildRailScale(state.thresholds);
    scale.dataset.built = '1';
  }

  scale.querySelectorAll('.tick').forEach((n) => n.remove());

  const wet = state.sensors.features
    .filter((f) => f.properties.depth_cm != null
                && f.properties.depth_cm >= state.thresholds.detect_cm)
    .sort((a, b) => a.properties.depth_cm - b.properties.depth_cm);

  let deepest = 0;
  for (const f of wet) {
    const cm = Number(f.properties.depth_cm);
    deepest = Math.max(deepest, cm);

    const tick = document.createElement('div');
    tick.className = 'tick';
    tick.style.bottom = `${Math.min(100, (cm / RAIL_MAX_CM) * 100)}%`;
    tick.style.background = depthColor(cm);
    tick.title = `${f.properties.name || f.properties.sensor_id} · ${cm.toFixed(1)} cm`;
    tick.dataset.sensor = f.properties.sensor_id;
    tick.addEventListener('click', () => {
      map.flyTo({ center: f.geometry.coordinates, zoom: 15, duration: 800 });
    });
    scale.appendChild(tick);
  }

  $('rail-water').style.height =
    `${Math.min(100, (deepest / RAIL_MAX_CM) * 100)}%`;
  $('rail-count').textContent =
    wet.length ? `${wet.length} wet · ${deepest.toFixed(1)} cm max`
               : 'No wet sensors';
}

/* --------------------------------------------------------------------------
   Advisory queue
   -------------------------------------------------------------------------- */

function paintQueue(state) {
  const list = $('queue-list');
  const items = state.advisories;
  $('queue-count').textContent = items.length ? `${items.length}` : '';

  if (!items.length) {
    list.innerHTML = `
      <div class="empty">
        <p class="empty-lead">No advisories issued.</p>
        <p class="empty-sub">
          The network is reporting${
            state.counts.sensors ? ` from ${state.counts.sensors} sensors` : ''
          } and no cluster has crossed
          ${state.thresholds.detect_cm} cm. Advisories appear here the moment
          two adjacent sensors report water together.
        </p>
      </div>`;
    return;
  }

  list.innerHTML = items.map((a) => {
    const fresh = !seenAdvisories.has(a.advisory_id) && !firstPaint;
    return `
      <article class="card${fresh ? ' fresh' : ''}"
               data-level="${a.level}" data-zone="${a.zone_id}"
               tabindex="0">
        <div class="card-top">
          <span class="card-level">${a.level}</span>
          <span class="card-time mono">${relativeTime(a.issued_at)}</span>
        </div>
        <div class="card-depth mono">
          ${a.max_depth_cm.toFixed(1)}<small>cm / ${cmToIn(a.max_depth_cm).toFixed(1)}in</small>
        </div>
        <div class="card-meta">
          <span>${a.sensor_count} sensors</span>
          <span>${a.state}</span>
          ${a.under_alert
            ? '<span class="corroborated">NWS confirmed</span>' : ''}
        </div>
      </article>`;
  }).join('');

  for (const a of items) seenAdvisories.add(a.advisory_id);

  list.querySelectorAll('.card').forEach((card) => {
    const focus = () => {
      const zone = latest.zones.features
        .find((f) => f.properties.zone_id === card.dataset.zone);
      if (!zone) return;
      const b = new maplibregl.LngLatBounds();
      for (const ring of zone.geometry.coordinates) {
        for (const c of ring) b.extend(c);
      }
      map.fitBounds(b, { padding: 160, maxZoom: 15.5, duration: 800 });
    };
    card.addEventListener('click', focus);
    card.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); focus(); }
    });
  });
}

/* --------------------------------------------------------------------------
   Header and status bar
   -------------------------------------------------------------------------- */

function paintChrome(state) {
  const wet = state.sensors.features
    .filter((f) => f.properties.depth_cm != null
                && f.properties.depth_cm >= state.thresholds.detect_cm);
  const deepest = wet.reduce(
    (m, f) => Math.max(m, Number(f.properties.depth_cm)), 0);

  $('stat-zones').textContent = state.counts.open_zones;
  $('stat-wet').textContent = `${wet.length}/${state.counts.sensors}`;
  $('stat-deepest').textContent = deepest ? `${deepest.toFixed(1)}cm` : '0cm';

  const alarm = deepest >= state.thresholds.warning_cm ? 'warning'
              : deepest >= state.thresholds.advisory_cm ? 'advisory' : '';
  $('stat-deepest').dataset.alarm = alarm;
  $('stat-zones').dataset.alarm = state.counts.open_zones ? alarm : '';

  const p = state.pipeline;
  $('q-ingest').textContent =
    `${p.queues.ingest.waiting}+${p.queues.ingest.in_flight}`;
  $('q-zones').textContent =
    `${p.queues.zones.waiting}+${p.queues.zones.in_flight}`;

  // Dead-letter depth is shown only when it is non-zero. A permanent "0" next
  // to the working queues would train the eye to skip it, which defeats the
  // point of surfacing it at all. Absent keys mean a stack provisioned before
  // the DLQ URLs were written to .env; that is unknown, not zero, so the row
  // stays hidden rather than claiming a clean queue. See E-024.
  const dlqCounts = ['ingest-dlq', 'zones-dlq']
    .map((k) => p.queues[k])
    .filter((q) => q && q.waiting > 0);
  const dlqTotal = dlqCounts.reduce((n, q) => n + q.waiting, 0);
  $('q-dlq-wrap').hidden = dlqTotal === 0;
  if (dlqTotal > 0) $('q-dlq').textContent = String(dlqTotal);

  $('pip-db').className = `pip ${p.database ? 'up' : 'down'}`;
  // Cache down is degraded, never down: every read falls through to Postgres.
  $('pip-cache').className = `pip ${p.cache.reachable ? 'up' : 'warn'}`;
  $('c-rate').textContent =
    p.cache.hit_rate == null ? 'n/a'
                             : `${(p.cache.hit_rate * 100).toFixed(0)}%`;

  $('s-source').textContent = p.source;
  $('s-readings').textContent = state.counts.readings.toLocaleString();
  $('s-advisories').textContent = state.counts.advisories;
  $('s-tick').textContent = new Date().toLocaleTimeString('en-US', {
    hour12: false,
  });
}

/* --------------------------------------------------------------------------
   Transport
   -------------------------------------------------------------------------- */

function setLink(cls, text) {
  $('pip-link').className = `pip ${cls}`;
  $('txt-link').textContent = text;
}

// The map layers are created before any state arrives, so their depth
// expressions start on placeholder stops and have to be corrected once the
// real thresholds land. Skipped when unchanged: this runs on every frame and
// setPaintProperty forces a repaint.
let appliedThresholds = null;

function thresholdsChanged(t) {
  const key = `${t.detect_cm}/${t.advisory_cm}/${t.warning_cm}`;
  if (key === appliedThresholds) return false;
  appliedThresholds = key;
  return true;
}

function waterRamp(t) {
  // Four stops spanning detection to warning, so the colour ramp means the
  // same thing on any source. Hardcoding 5/12/20/30 made every gauge with a
  // 5 cm stage rise paint as flooded under the usgs default, where detection
  // does not start until 60. That is E-014 surviving in the frontend after it
  // was fixed in the API. See E-022.
  const span = Math.max(t.warning_cm - t.detect_cm, 1);
  return [
    'interpolate', ['linear'], ['get', 'depth_cm'],
    t.detect_cm, WATER[0],
    t.detect_cm + span * 0.35, WATER[1],
    t.advisory_cm, WATER[2],
    t.warning_cm, WATER[3],
  ];
}

function applyThresholds(t) {
  if (!mapReady || !thresholdsChanged(t)) return;

  map.setFilter('sensor-dry', ['any',
    ['==', ['get', 'depth_cm'], null],
    ['<', ['get', 'depth_cm'], t.detect_cm]]);
  map.setFilter('sensor-wet', ['all',
    ['!=', ['get', 'depth_cm'], null],
    ['>=', ['get', 'depth_cm'], t.detect_cm]]);

  map.setPaintProperty('sensor-wet', 'circle-color', waterRamp(t));
  map.setPaintProperty('sensor-wet', 'circle-radius', [
    'interpolate', ['linear'], ['get', 'depth_cm'],
    t.detect_cm, 4, t.advisory_cm, 8.5, t.warning_cm, 13,
  ]);

  const zoneRamp = waterRamp(t).slice();
  zoneRamp[2] = ['get', 'max_depth_cm'];
  map.setPaintProperty('zone-fill', 'fill-color', zoneRamp);
}

function apply(state) {
  latest = state;
  applyThresholds(state.thresholds);
  paintChrome(state);
  paintRail(state);
  paintQueue(state);
  paintMap(state);
  if (firstPaint && !state.zones.features.length) firstPaint = false;
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${proto}://${location.host}/ws`);
  let keepalive = null;

  socket.addEventListener('open', () => {
    setLink('up', 'Receiving');
    keepalive = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send('ping');
    }, 25000);
  });

  socket.addEventListener('message', (e) => {
    try {
      apply(JSON.parse(e.data));
    } catch (err) {
      console.error('bad state payload', err);
    }
  });

  const retry = () => {
    clearInterval(keepalive);
    setLink('warn', 'Reconnecting');
    setTimeout(connect, 3000);
  };

  socket.addEventListener('close', retry);
  socket.addEventListener('error', () => socket.close());
}

// Timestamps go stale between ticks, so refresh the relative labels locally.
setInterval(() => {
  if (!latest) return;
  document.querySelectorAll('.card').forEach((card, i) => {
    const a = latest.advisories[i];
    if (a) card.querySelector('.card-time').textContent =
      relativeTime(a.issued_at);
  });
}, 10000);

initMap();
connect();
