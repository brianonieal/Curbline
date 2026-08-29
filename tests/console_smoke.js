/* ==========================================================================
   Console smoke test. No browser, no AWS.

   Why this exists: seven of the twenty-two evidence screenshots are the
   console, and the console is the only component with no automated coverage at
   all. A JavaScript exception during a capture session costs the session, and
   the stack bills hourly while you work out which line threw.

   It loads web/app.js into a stubbed DOM and a stubbed MapLibre, then drives
   apply() with the payload shapes the real API produces: an empty first tick, a
   full storm, a degraded cache, and several deliberately malformed states. A
   thrown exception fails the run.

   It also asserts the E-022 fix directly, which is otherwise unverified: the
   map layers must be re-expressed from state.thresholds rather than keeping
   the FloodNet literals compiled into the layer definitions.

       node tests/console_smoke.js
   ========================================================================== */

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = path.join(__dirname, '..', 'web', 'app.js');

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (e) { failures++; console.log(`  FAIL ${name}\n       ${e.message}`); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

// --- Stubs -----------------------------------------------------------------

function fakeElement(id) {
  const el = {
    id, textContent: '', innerHTML: '', className: '', hidden: false,
    dataset: {}, style: {},
    children: [],
    appendChild(c) { this.children.push(c); return c; },
    remove() {},
    querySelector() { return fakeElement('child'); },
    querySelectorAll() { return []; },
    addEventListener() {},
    getBoundingClientRect() { return { top: 0, left: 0, width: 100, height: 100 }; },
  };
  return el;
}

function makeContext() {
  const elements = new Map();
  const paint = {};      // layer -> { property: value }
  const filters = {};    // layer -> filter
  const sources = {};

  const mapStub = {
    addControl() {}, addLayer() {}, fitBounds() {}, flyTo() {},
    getCanvas() { return { style: {} }; },
    addSource(id) { sources[id] = { setData() {} }; },
    getSource(id) { return sources[id] || { setData() {} }; },
    on(evt, cb) { if (evt === 'load') mapStub._load = cb; },
    setPaintProperty(layer, prop, val) {
      (paint[layer] = paint[layer] || {})[prop] = val;
    },
    setFilter(layer, f) { filters[layer] = f; },
    getLayer(id) { return { id }; },
  };

  const ctx = {
    console,
    setTimeout() {}, clearTimeout() {},
    setInterval() {}, clearInterval() {},
    location: { protocol: 'http:', host: 'localhost:8000' },
    WebSocket: function () {
      return { addEventListener() {}, send() {}, close() {}, readyState: 1 };
    },
    maplibregl: {
      Map: function () { return mapStub; },
      NavigationControl: function () { return {}; },
      LngLatBounds: function () {
        return { extend() { return this; }, isEmpty() { return false; } };
      },
      Popup: function () {
        return { setLngLat() { return this; }, setHTML() { return this; },
                 addTo() { return this; }, remove() { return this; } };
      },
    },
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, fakeElement(id));
        return elements.get(id);
      },
      querySelectorAll() { return []; },
      createElement(tag) { return fakeElement(tag); },
      addEventListener() {},
      body: fakeElement('body'),
    },
  };
  ctx.window = ctx;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  ctx.__paint = paint;
  ctx.__filters = filters;
  ctx.__elements = elements;
  ctx.__map = mapStub;
  return ctx;
}

function load() {
  const ctx = makeContext();
  vm.runInContext(fs.readFileSync(APP, 'utf8'), ctx, { filename: 'app.js' });
  // initMap() registered a load handler; fire it so the layers exist and
  // mapReady flips, which is what the real page does.
  if (ctx.__map._load) ctx.__map._load();
  return ctx;
}

// --- Payload builders, matching api/server.py build_state() ----------------

const fc = (features = []) => ({ type: 'FeatureCollection', features });

function emptyState(thresholds) {
  return {
    sensors: fc(), zones: fc(), alerts: fc(), advisories: [],
    counts: { sensors: 0, readings: 0, open_zones: 0, advisories: 0,
              active_alerts: 0 },
    thresholds: thresholds ||
      { detect_cm: 60, advisory_cm: 90, curb_cm: 105, warning_cm: 120 },
    pipeline: {
      queues: { ingest: { waiting: 0, in_flight: 0 },
                zones: { waiting: 0, in_flight: 0 } },
      cache: { hits: 0, misses: 0, errors: 0, hit_rate: null, reachable: true },
      database: true, source: 'usgs',
    },
  };
}

function stormState() {
  const s = emptyState();
  s.sensors = fc([{
    type: 'Feature', geometry: { type: 'Point', coordinates: [-73.79, 40.70] },
    properties: { sensor_id: 'usgs:1', name: 'Test', depth_cm: 95.0,
                  observed_at: '2026-08-28T12:00:00+00:00', zone_id: 'z1' },
  }]);
  s.zones = fc([{
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
    properties: { zone_id: 'z1', sensor_ids: ['usgs:1'], sensor_count: 2,
                  max_depth_cm: 95.0, state: 'active', under_alert: true,
                  alert_id: 'a1', opened_at: '2026-08-28T12:00:00+00:00',
                  updated_at: '2026-08-28T12:00:00+00:00' },
  }]);
  s.advisories = [{
    advisory_id: 'adv1', zone_id: 'z1', level: 'warning',
    message: 'CURBLINE WARNING', issued_at: '2026-08-28T12:00:00+00:00',
    audit_key: 'advisories/2026/08/28/z1/x.json', state: 'active',
    sensor_count: 2, max_depth_cm: 95.0, under_alert: true,
  }];
  s.counts = { sensors: 1, readings: 100, open_zones: 1, advisories: 1,
               active_alerts: 1 };
  return s;
}

// --- Tests -----------------------------------------------------------------

console.log('console smoke');

check('loads without throwing', () => { load(); });

check('empty first tick paints', () => {
  const ctx = load();
  ctx.apply(emptyState());
});

check('storm tick paints', () => {
  const ctx = load();
  ctx.apply(stormState());
});

check('repeated ticks are stable', () => {
  const ctx = load();
  ctx.apply(emptyState());
  ctx.apply(stormState());
  ctx.apply(stormState());
  ctx.apply(emptyState());
});

// E-022. The whole point of the fix.
check('E-022: layers are re-expressed from state.thresholds', () => {
  const ctx = load();
  ctx.apply(emptyState());   // usgs: detect 60
  const wet = ctx.__filters['sensor-wet'];
  assert(wet, 'sensor-wet filter was never rewritten from thresholds');
  const flat = JSON.stringify(wet);
  assert(flat.includes('60'),
    `sensor-wet must threshold at detect_cm 60, got ${flat}`);
  assert(!flat.includes(',5]') && !flat.includes(', 5]'),
    `sensor-wet still carries the FloodNet literal 5: ${flat}`);
});

check('E-022: the colour ramp spans detect to warning', () => {
  const ctx = load();
  ctx.apply(emptyState());
  const ramp = JSON.stringify(ctx.__paint['sensor-wet']['circle-color']);
  assert(ramp.includes('60') && ramp.includes('120'),
    `ramp must span detect 60 to warning 120, got ${ramp}`);
});

check('E-022: switching source re-expresses the layers', () => {
  const ctx = load();
  ctx.apply(emptyState());
  ctx.apply(emptyState({ detect_cm: 5, advisory_cm: 10, curb_cm: 15,
                         warning_cm: 20 }));
  const ramp = JSON.stringify(ctx.__paint['sensor-wet']['circle-color']);
  assert(ramp.includes('20'),
    `ramp must follow a threshold change, got ${ramp}`);
});

check('E-022: identical thresholds do not repaint every tick', () => {
  const ctx = load();
  ctx.apply(emptyState());
  const first = ctx.__paint['sensor-wet']['circle-color'];
  ctx.__paint['sensor-wet']['circle-color'] = 'SENTINEL';
  ctx.apply(emptyState());
  assert(ctx.__paint['sensor-wet']['circle-color'] === 'SENTINEL',
    'unchanged thresholds must not force a repaint');
  assert(first, 'first paint should have happened');
});

// E-024. The indicator is hidden while zero and must appear when not.
check('E-024: dead-letter indicator hidden at zero', () => {
  const ctx = load();
  const s = emptyState();
  s.pipeline.queues['ingest-dlq'] = { waiting: 0, in_flight: 0 };
  s.pipeline.queues['zones-dlq'] = { waiting: 0, in_flight: 0 };
  ctx.apply(s);
  assert(ctx.__elements.get('q-dlq-wrap').hidden === true,
    'a zero dead-letter queue must stay hidden');
});

check('E-024: dead-letter indicator appears and totals', () => {
  const ctx = load();
  const s = emptyState();
  s.pipeline.queues['ingest-dlq'] = { waiting: 2, in_flight: 0 };
  s.pipeline.queues['zones-dlq'] = { waiting: 3, in_flight: 0 };
  ctx.apply(s);
  const el = ctx.__elements.get('q-dlq-wrap');
  assert(el.hidden === false, 'a non-empty dead-letter queue must be visible');
  assert(ctx.__elements.get('q-dlq').textContent === '5',
    `expected total 5, got ${ctx.__elements.get('q-dlq').textContent}`);
});

check('E-024: absent dlq keys stay hidden rather than claiming clean', () => {
  const ctx = load();
  ctx.apply(emptyState());   // no -dlq keys at all
  assert(ctx.__elements.get('q-dlq-wrap').hidden === true,
    'unknown must not render as a visible zero');
});

// Degraded states. These are screenshots, so they must not throw.
check('unreachable cache renders', () => {
  const ctx = load();
  const s = stormState();
  s.pipeline.cache = { hits: 0, misses: 0, errors: 9, hit_rate: null,
                       reachable: false };
  ctx.apply(s);
});

check('null hit rate renders as n/a, not 0%', () => {
  const ctx = load();
  ctx.apply(emptyState());
  assert(ctx.__elements.get('c-rate').textContent === 'n/a',
    `expected n/a, got ${ctx.__elements.get('c-rate').textContent}`);
});

check('database down renders', () => {
  const ctx = load();
  const s = stormState();
  s.pipeline.database = false;
  ctx.apply(s);
});

check('queue probe failure (-1) renders', () => {
  const ctx = load();
  const s = stormState();
  s.pipeline.queues.ingest = { waiting: -1, in_flight: -1 };
  ctx.apply(s);
});

check('a forming zone renders', () => {
  const ctx = load();
  const s = stormState();
  s.zones.features[0].properties.state = 'forming';
  ctx.apply(s);
});

check('a zone with no alert renders', () => {
  const ctx = load();
  const s = stormState();
  s.zones.features[0].properties.under_alert = false;
  s.zones.features[0].properties.alert_id = null;
  s.advisories[0].under_alert = false;
  ctx.apply(s);
});

check('a null sensor depth renders', () => {
  const ctx = load();
  const s = stormState();
  s.sensors.features[0].properties.depth_cm = null;
  ctx.apply(s);
});

console.log(failures === 0
  ? '\nconsole smoke: all passed'
  : `\nconsole smoke: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
