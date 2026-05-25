const $ = (id) => document.getElementById(id);

let serverUrl = '';
let latest = null;
let ws = null;
let reconnectTimer = null;
let wsGeneration = 0;
const pressed = new Set();
let lastInputMode = 'overview';

const ROTATION_STEP_DEG = 15;
const STOP_INPUT_KEY = '0.000,0.000,1';
let lastMoveInput = STOP_INPUT_KEY;

function normalizeServerUrl(value) {
  const trimmed = String(value || '').trim().replace(/\/+$/, '');
  if (!trimmed) throw new Error('Server URL is required.');
  const url = new URL(trimmed);
  if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Use http:// or https://.');
  return url.toString().replace(/\/+$/, '');
}

function setConnectedUi(connected) {
  $('connectPanel').classList.toggle('hidden', connected);
  $('connectionStatus').textContent = connected ? `Connected: ${serverUrl}` : 'Disconnected';
}

function disconnectForSettings() {
  sendMoveInput(0, 0, true);
  wsGeneration += 1;
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  if (ws) {
    ws.close();
    ws = null;
  }
  $('videoStream').removeAttribute('src');
  setConnectedUi(false);
}

async function api(path, body) {
  const res = await fetch(`${serverUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return render(await res.json());
}

async function connect(urlCandidate) {
  const nextUrl = normalizeServerUrl(urlCandidate);
  const health = await fetch(`${nextUrl}/health`);
  if (!health.ok) throw new Error(`Health check failed: ${health.status}`);
  serverUrl = nextUrl;
  localStorage.setItem('homesense.serverUrl', serverUrl);
  $('videoStream').src = `${serverUrl}/video.mjpg`;
  setConnectedUi(true);
  await fetch(`${serverUrl}/state`).then((res) => res.json()).then(render);
  connectWs();
}

function connectWs() {
  wsGeneration += 1;
  const generation = wsGeneration;
  if (ws) ws.close();
  clearTimeout(reconnectTimer);
  const protocol = serverUrl.startsWith('https:') ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${new URL(serverUrl).host}/ws`;
  ws = new WebSocket(wsUrl);
  ws.addEventListener('open', () => $('connectionStatus').textContent = `Connected: ${serverUrl}`);
  ws.addEventListener('message', (event) => render(JSON.parse(event.data)));
  ws.addEventListener('close', () => {
    if (generation !== wsGeneration || !serverUrl) return;
    $('connectionStatus').textContent = 'Reconnecting';
    reconnectTimer = setTimeout(connectWs, 1000);
  });
}

function render(data) {
  latest = data;
  const motion = data.motion || {};
  const pressure = data.pressure || {};
  const human = data.human || {};
  const robot = data.robot || {};
  const video = data.video || {};
  const sensorVisualization = data.sensor_visualization || {};
  const busy = Boolean(robot.busy);
  const inputMode = getInputMode(data);

  $('motionState').textContent = motion.detected
    ? `Motion ${motion.active_sensor_id || motion.sensor_id} ${Number(motion.distance_m || 0).toFixed(2)}m`
    : 'Motion OFF';
  $('residentState').textContent = Array.isArray(human.position)
    ? `Resident ${(human.zone || 'unknown')} ${human.position.map((v) => Number(v).toFixed(2)).join(', ')}`
    : 'Resident --';
  $('pressureState').textContent = `Pressure ${pressure.triggered ? 'ON' : 'OFF'} ${Number(pressure.weight_kg || 0).toFixed(1)}kg`;
  $('cameraState').textContent = `Camera ${data.camera_mode || '--'} / ${video.source || 'viewer'}`;
  $('robotState').textContent = robot.status || 'idle';
  renderInputHint(inputMode, busy);
  $('runTaskButton').disabled = busy;
  $('taskSelect').disabled = busy;
  if ((busy || inputMode === 'locked') && lastMoveInput !== STOP_INPUT_KEY) sendMoveInput(0, 0, true);

  const selectValue = `${video.source || 'viewer'}:${data.camera_mode || 'overview'}`;
  if ([...$('cameraSelect').options].some((option) => option.value === selectValue)) {
    $('cameraSelect').value = selectValue;
  }
  $('sensorRangeToggle').checked = Boolean(sensorVisualization.motion_ranges_visible);
  if (inputMode !== lastInputMode) {
    pressed.clear();
    lastInputMode = inputMode;
    sendMoveInput(0, 0, true);
  }

  return data;
}

function getInputMode(data = latest) {
  const video = data && data.video ? data.video : {};
  const source = video.source || 'viewer';
  const mode = data ? data.camera_mode || 'overview' : 'overview';
  if (source === 'robot' || mode === 'robot') return 'locked';
  if (mode === 'resident') return 'resident';
  return 'overview';
}

function inputStatusText(inputMode, busy) {
  if (busy) return 'Resident input locked';
  if (inputMode === 'locked') return '조작 불가';
  if (inputMode === 'resident') return 'WASD move / Q E turn';
  return 'WASD';
}

function renderInputHint(inputMode, busy) {
  const keyGrid = $('inputKeys');
  keyGrid.className = `keyGrid ${inputMode}`;
  const keys = inputMode === 'overview'
    ? ['w', 'a', 's', 'd']
    : inputMode === 'resident'
      ? ['q', 'w', 'e', 'a', 's', 'd']
      : [];
  keyGrid.innerHTML = keys
    .map((key) => `<kbd class="key${key.toUpperCase()}">${key.toUpperCase()}</kbd>`)
    .join('');
  $('inputState').textContent = inputStatusText(inputMode, busy);
}

function getMoveVector() {
  let dx = 0;
  let dy = 0;
  const inputMode = getInputMode();
  if (inputMode === 'locked') {
    return [0, 0, true];
  }
  if (inputMode === 'resident') {
    const human = latest && latest.human ? latest.human : {};
    const heading = Number(human.heading_deg || 0) * Math.PI / 180;
    const forward = [-Math.sin(heading), Math.cos(heading)];
    const right = [Math.cos(heading), Math.sin(heading)];
    if (pressed.has('w')) {
      dx += forward[0];
      dy += forward[1];
    }
    if (pressed.has('s')) {
      dx -= forward[0];
      dy -= forward[1];
    }
    if (pressed.has('a')) {
      dx -= right[0];
      dy -= right[1];
    }
    if (pressed.has('d')) {
      dx += right[0];
      dy += right[1];
    }
  } else {
    if (pressed.has('w')) dx += 1;
    if (pressed.has('s')) dx -= 1;
    if (pressed.has('a')) dy += 1;
    if (pressed.has('d')) dy -= 1;
  }
  if (dx && dy) {
    dx *= Math.SQRT1_2;
    dy *= Math.SQRT1_2;
  }
  return [dx, dy, inputMode !== 'resident'];
}

function sendMoveInput(dx, dy, force = false, faceMovement = true) {
  const key = `${dx.toFixed(3)},${dy.toFixed(3)},${faceMovement ? 1 : 0}`;
  if (!force && key === lastMoveInput) return;
  lastMoveInput = key;
  if (!serverUrl || (latest && latest.robot && latest.robot.busy && (dx || dy))) return;
  fetch(`${serverUrl}/command/set-human-input`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dx, dy, dz: 0, face_movement: faceMovement }),
  }).catch(() => undefined);
}

function updateMoveInput(force = false) {
  if (!serverUrl) return;
  if ((latest && latest.robot && latest.robot.busy) || getInputMode() === 'locked') {
    sendMoveInput(0, 0, true);
    return;
  }
  const [dx, dy, faceMovement] = getMoveVector();
  sendMoveInput(dx, dy, force, faceMovement);
}

function rotateResident(deltaDeg) {
  if (!serverUrl || getInputMode() !== 'resident' || (latest && latest.robot && latest.robot.busy)) return;
  api('/command/rotate-human-heading', { delta_deg: deltaDeg })
    .then(() => updateMoveInput(true))
    .catch(() => undefined);
}

window.addEventListener('keydown', (event) => {
  if (['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(event.target.tagName)) return;
  const key = event.key.toLowerCase();
  if (!['w', 'a', 's', 'd', 'q', 'e'].includes(key)) return;
  event.preventDefault();
  if (key === 'q' || key === 'e') {
    if (!event.repeat) rotateResident(key === 'q' ? ROTATION_STEP_DEG : -ROTATION_STEP_DEG);
    return;
  }
  pressed.add(key);
  updateMoveInput();
});

window.addEventListener('keyup', (event) => {
  pressed.delete(event.key.toLowerCase());
  updateMoveInput();
});

window.addEventListener('blur', () => {
  pressed.clear();
  updateMoveInput(true);
});

$('connectButton').addEventListener('click', () => {
  $('connectPanel').classList.remove('error');
  $('connectMessage').textContent = 'Connecting';
  connect($('serverUrlInput').value)
    .then(() => $('connectMessage').textContent = 'Connected')
    .catch((error) => {
      $('connectPanel').classList.add('error');
      $('connectMessage').textContent = error.message;
    });
});

$('serverUrlInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') $('connectButton').click();
});

$('settingsButton').addEventListener('click', () => {
  $('serverUrlInput').value = serverUrl;
  disconnectForSettings();
});

$('cameraSelect').addEventListener('change', (event) => {
  pressed.clear();
  sendMoveInput(0, 0, true);
  const [source, mode] = event.target.value.split(':');
  api('/command/set-video-source', { source }).catch(() => undefined);
  api('/command/set-camera', { mode }).catch(() => undefined);
});

$('sensorRangeToggle').addEventListener('change', (event) => {
  api('/command/set-sensor-ranges-visible', { visible: event.target.checked }).catch(() => undefined);
});

$('runTaskButton').addEventListener('click', () => {
  api('/command/run-task', { task: $('taskSelect').value }).catch(() => undefined);
});

$('resetButton').addEventListener('click', () => api('/command/reset', {}).catch(() => undefined));

window.homeSense.getDefaultServer().then((defaultServer) => {
  const saved = localStorage.getItem('homesense.serverUrl') || defaultServer;
  $('serverUrlInput').value = saved;
  connect(saved).catch(() => setConnectedUi(false));
});
