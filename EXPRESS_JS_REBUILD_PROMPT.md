# Express.js Rebuild — AI Prompt & Task Specification

> **Context:** This document is a complete specification for rebuilding an existing Django-based
> scientific experiment monitoring web application using **Node.js + Express.js**.
> Paste the entire document (or the relevant sections) into your AI assistant (ChatGPT, Codex, etc.)
> and it will generate the project for you.

---

## 1. Project Overview

Build a **web application** for managing and monitoring scientific experiments that collect real-time
telemetry data from an **Arduino microcontroller** connected via a **serial (UART) port** to a
**Raspberry Pi** (or any Linux machine).

The system must:
- Run directly with **Node.js** (no Docker).
- Start with `node server.js` or `npm start`.
- Allow users to **create, view, start, finish, and abort** experiments.
- **Poll the Arduino** for sensor data at high frequency (up to 20 Hz) using the `READ_ALL` serial command.
- **Store telemetry frames** (time-series measurements) in a **SQLite** database using **Sequelize ORM**.
- Expose a **REST JSON API** for clients (e.g., the Pi collector script) to push telemetry batches.
- Render **server-side HTML** pages using the **EJS** templating engine.
- Be **containerized with Docker** and support a separate collector service profile.

---

## 2. Tech Stack Rules

| Layer | Technology | Notes |
|---|---|---|
| Runtime | Node.js ≥ 20 LTS | |
| Web framework | **Express.js** v4 | |
| Database | SQLite (file-based) | |
| ORM | **Sequelize** v6 | Dialect: `sqlite` |
| Templates | **EJS** | Server-side rendered HTML |
| Serial port | **serialport** npm package | For Arduino communication |
| Process management | Native Node.js threads/workers | Use `worker_threads` or background intervals for poller |

**Do NOT use:**
- TypeScript (JavaScript only)
- React, Vue, Angular, or any frontend framework
- Any database other than SQLite
- Any ORM other than Sequelize
- Docker or any containerization

---

## 3. Data Models (Sequelize)

### 3.1 `Experiment` Model

| Field | Type | Constraints / Default |
|---|---|---|
| `id` | INTEGER | Primary key, auto-increment |
| `title` | STRING(200) | Default: `"Untitled"` |
| `description` | TEXT | Nullable, empty string default |
| `status` | ENUM | `draft`, `ready`, `running`, `finished`, `aborted`, `failed`; Default: `draft` |
| `createdAt` | DATE | Auto-managed by Sequelize |
| `updatedAt` | DATE | Auto-managed by Sequelize |
| `startedAt` | DATE | Nullable |
| `ignitedAt` | DATE | Nullable |
| `endedAt` | DATE | Nullable |
| `serialPort` | STRING(128) | Default: `""` |
| `baudRate` | INTEGER | Default: `115200` |
| `color` | STRING(7) | Default: `"#0d9488"` (hex color) |

### 3.2 `Frame` Model

| Field | Type | Constraints / Default |
|---|---|---|
| `id` | INTEGER | Primary key, auto-increment |
| `experimentId` | INTEGER | Foreign key → `Experiment.id`, CASCADE delete |
| `second` | FLOAT | NOT NULL — time offset from experiment start (seconds) |
| `temperature` | FLOAT | NOT NULL |
| `difPressure` | FLOAT | NOT NULL |
| `rpm` | FLOAT | Default: `0` |
| `receivedAt` | DATE | Default: `NOW` |

**Relations:** `Experiment.hasMany(Frame)` / `Frame.belongsTo(Experiment)`.

---

## 4. REST API Endpoints

All API responses must use `Content-Type: application/json`.

### 4.1 UI Routes (server-side rendered EJS)

| Method | Path | Description |
|---|---|---|
| GET | `/` | Redirect to `/experiments` |
| GET | `/experiments` | List all experiments (last 50, newest first) |
| GET | `/experiments/new` | Render create form |
| POST | `/experiments/new` | Process create form, redirect to detail page |
| GET | `/experiments/:id` | Experiment detail / live telemetry dashboard |
| POST | `/experiments/:id/action` | Perform lifecycle action (body: `action=start\|ignite\|finish\|abort`) |
| POST | `/experiments/:id/delete` | Delete experiment, redirect to list |
| POST | `/experiments/:id/update-color` | Update color (body: `color=#rrggbb`), return JSON `{status:"ok", color}` |

### 4.2 JSON API Routes

| Method | Path | Description |
|---|---|---|
| GET | `/api/experiments/:id/summary` | Return experiment metadata + last frame + frame count |
| GET | `/api/experiments/:id/frames` | Return frames array (query param: `?limit=500`) |
| POST | `/api/experiments/:id/frames/batch` | Ingest batch of frames (no CSRF required) |
| POST | `/api/experiments/:id/command` | Send serial command (`start`\|`stop`) to Arduino |
| POST | `/api/experiments/:id/test-connection` | Send `PING` to Arduino, return result |

#### `POST /api/experiments/:id/frames/batch` — Request Body Format

Accept two formats:
1. `{ "frames": [ {...}, {...} ] }`
2. `[ {...}, {...} ]` (bare array)

Each frame object must contain: `second`, `temperature`, `dif_pressure`.
Optional: `rpm` (default `0`).

Response on success: `{ "status": "ok", "created": <number> }` HTTP 201.

#### `POST /api/experiments/:id/command` — Request Body
```json
{ "command": "start" }
```
or
```json
{ "command": "stop" }
```

#### `/api/experiments/:id/summary` — Response Shape
```json
{
  "status": "ok",
  "experiment": {
    "id": 1,
    "title": "...",
    "description": "...",
    "status": "running",
    "createdAt": "...",
    "updatedAt": "...",
    "startedAt": "...",
    "ignitedAt": "...",
    "endedAt": "...",
    "serialPort": "/dev/ttyUSB0",
    "baudRate": 115200
  },
  "frames": {
    "count": 1234,
    "last": {
      "second": 12.5,
      "temperature": 23.1,
      "difPressure": 101.3,
      "rpm": 3500,
      "receivedAt": "..."
    }
  }
}
```

---

## 5. Experiment Lifecycle (Action Logic)

When `POST /experiments/:id/action` is called with `action` in the body:

| Action | What to do |
|---|---|
| `start` | Set `startedAt = NOW` (if null), set `status = running`, start background poller (if `serialPort` is set) |
| `ignite` | Set `ignitedAt = NOW` (if null), set `startedAt = NOW` (if null), set `status = running`, start poller |
| `finish` | Set `endedAt = NOW` (if null), set `status = finished`, stop poller |
| `abort` | Set `endedAt = NOW` (if null), set `status = aborted`, stop poller |

After the action, redirect back to `GET /experiments/:id`.

---

## 6. Arduino Serial Protocol

### 6.1 Commands & Responses

| Command sent | Expected response |
|---|---|
| `PING\n` | Line starting with `OK` |
| `START\n` | Line starting with `OK` |
| `STOP\n` | Line starting with `OK` |
| `READ_ALL\n` | `OK DATA <t_ms> <rpm> <pressure> <temperature> <mosfet>` |

**Response parsing rules:**
- If any received line starts with `ERR` → failure.
- If any received line starts with `OK` → success / confirmed.
- Timeout after N seconds → failure with error `"Timeout waiting for response."`.

### 6.2 `READ_ALL` Response Format

```
OK DATA <t_ms> <rpm> <pressure_pa> <temperature_c> <mosfet>
```

Example: `OK DATA 12500 3500 101325 23.1 1`

Parse with regex:
```
/^OK\s+DATA\s+(\d+)\s+(\d+)\s+(\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(\d+)/i
```

Extract:
- `second = t_ms / 1000.0`
- `rpm`
- `difPressure = pressure_pa`
- `temperature = temperature_c`

### 6.3 Serial Session Management

- Maintain **one persistent connection per `(port, baudRate)` pair** (singleton map).
- Re-opening the port resets Arduino (hardware DTR/RTS toggle) — keep it open.
- On first open, wait **2.2 seconds** for Arduino to boot, then flush the input buffer.
- All serial I/O must be **thread-safe** (use a mutex/lock per session).
- Use the **`serialport`** npm package.

---

## 7. Background Poller

Create a background service (`telemetry.js` or `poller.js`) that:

1. Runs in a **daemon-like background interval** (not blocking the main event loop — use `setInterval` with a proper async guard, or a `worker_thread`).
2. Polls at **20 Hz** (every 50 ms) using `READ_ALL`.
3. Accumulates frames in a buffer; when buffer reaches **20 frames**, bulk-inserts them into the DB using `Frame.bulkCreate()`.
4. Checks the experiment's `status` on each iteration — stops if not `running`.
5. On stop, flushes remaining buffered frames to DB.
6. Is started by `ensurePollerRunning(experiment)` and stopped by `stopPoller(experimentId)`.

**Poller registry:** a `Map<experimentId, pollerHandle>` to prevent duplicate pollers.

---

## 8. Raspberry Pi Collector Script

Create `scripts/pi_collector.js` (standalone Node.js script, not part of Express app):

- Reads env variables: `EXPERIMENT_ID`, `SERVER_BASE_URL`, `SERIAL_PORT`, `BAUD_RATE` (default 115200), `POLL_HZ` (default 20), `BATCH_SIZE` (default 20).
- If `SERIAL_PORT` is not set, fetches it from `GET /api/experiments/:id/summary`.
- Opens the serial port and polls with `READ_ALL` at `POLL_HZ`.
- Accumulates frames; when batch reaches `BATCH_SIZE`, POSTs to `POST /api/experiments/:id/frames/batch`.
- Handles serial errors gracefully (retry with backoff).
- Run directly: `node scripts/pi_collector.js` (no Docker needed).

---

## 9. HTML Templates (EJS)

Create the following EJS templates in `views/` directory:

### 9.1 `views/layout.ejs` (base layout)
- Include a `<nav>` with the app name and a link to `/experiments`.
- Load styles from `public/style.css`.
- Use a `<%- body %>` placeholder for page content (or use `express-ejs-layouts`).

### 9.2 `views/experiments/list.ejs`
- Show a table/card list of experiments.
- Each row: title, status (colored badge), created date, link to detail page.
- "New Experiment" button linking to `/experiments/new`.

### 9.3 `views/experiments/create.ejs`
- Form with fields: `title`, `description`, `color` (color picker), `serialPort`, `baudRate`.
- POST to `/experiments/new`.
- Color picker showing preset swatches:
  ```
  #0ea5e9, #3b82f6, #6366f1, #8b5cf6, #d946ef, #ec4899,
  #f43f5e, #f97316, #eab308, #22c55e, #14b8a6, #64748b
  ```

### 9.4 `views/experiments/detail.ejs`
- Show experiment metadata (title, status, timestamps, serial config).
- Lifecycle action buttons: Start / Ignite / Finish / Abort / Delete (as POST forms).
- **Live telemetry dashboard** — using `setInterval` + `fetch` to poll:
  - `GET /api/experiments/:id/summary` every 1 second.
  - `GET /api/experiments/:id/frames?limit=500` every 2 seconds.
- Display:
  - Current temperature, differential pressure, RPM as large metric cards.
  - Chart(s) over time using **Chart.js** (loaded from CDN).
  - Frame count.
- Color update control (color swatches that AJAX-POST to `/experiments/:id/update-color`).
- Test Connection button (AJAX POST to `/api/experiments/:id/test-connection`).

---

## 10. Project File Structure

```
express-experiment-monitor/
├── app.js                    # Express app setup (no listen)
├── server.js                 # HTTP server entry point (calls app.listen)
├── sequelize.js              # Sequelize instance + DB init
├── models/
│   ├── Experiment.js
│   └── Frame.js
├── routes/
│   ├── experiments.js        # UI routes
│   └── api.js                # JSON API routes
├── services/
│   ├── arduino.js            # Serial communication helpers
│   └── telemetry.js          # ArduinoSession, ExperimentPoller, poller registry
├── views/
│   ├── layout.ejs
│   └── experiments/
│       ├── list.ejs
│       ├── create.ejs
│       └── detail.ejs
├── public/
│   └── style.css
├── scripts/
│   └── pi_collector.js
├── .env.example
├── .gitignore
├── package.json
└── README.md
```

---

## 11. Environment Variables

| Variable | Default | Description |
|---|---|---|
| `NODE_ENV` | `development` | `production` disables verbose errors |
| `PORT` | `3000` | HTTP server port |
| `SQLITE_PATH` | `./data/db.sqlite` | Path to SQLite database file |
| `SECRET_SESSION_KEY` | — | Required in production for session signing |

---

## 12. Running the App

Start the web server:
```sh
npm install
node server.js
# or
npm start
```

Run the Raspberry Pi collector standalone:
```sh
EXPERIMENT_ID=1 SERVER_BASE_URL=http://localhost:3000 SERIAL_PORT=/dev/ttyUSB0 node scripts/pi_collector.js
```

---

## 13. package.json Dependencies

```json
{
  "dependencies": {
    "express": "^4.18.0",
    "sequelize": "^6.37.0",
    "sqlite3": "^5.1.7",
    "ejs": "^3.1.10",
    "serialport": "^12.0.0",
    "body-parser": "^1.20.0",
    "method-override": "^3.0.0",
    "dotenv": "^16.4.0"
  }
}
```

Use `npm install` to install.

---

## 14. Key Implementation Rules

1. **Sequelize sync:** Call `sequelize.sync()` on startup (in `server.js`) before `app.listen()`.
2. **Body parsing:** Use `express.urlencoded({ extended: false })` for form POSTs and `express.json()` for API POSTs.
3. **No CSRF** on the batch ingest endpoint (`POST /api/experiments/:id/frames/batch`) — it is called by an external collector script.
4. **Error handling:** Add a global Express error handler that returns `500` with a JSON or HTML error message.
5. **Serial lock:** Each `ArduinoSession` must serialize writes/reads using an async mutex (e.g., a simple Promise-based queue/lock) — never read and write simultaneously.
6. **Poller isolation:** The poller `setInterval` must catch all errors internally and never crash the process.
7. **Bulk insert:** Use `Frame.bulkCreate(frames, { validate: false })` for performance.
8. **DB path:** Read from `process.env.SQLITE_PATH` so it can be mounted as a Docker volume.
9. **Static files:** Serve `public/` via `express.static('public')`.
10. **Port redirect:** `GET /` must redirect `302` to `/experiments`.

---

## 15. Acceptance Criteria

The project is considered complete when:

- [ ] `npm start` starts the server without errors on port 3000.
- [ ] A user can create a new experiment with title, description, color, serial port, baud rate.
- [ ] The experiments list page shows all experiments with their status and creation date.
- [ ] The detail page auto-updates every second via AJAX without full page reload.
- [ ] Starting an experiment launches the background Arduino poller.
- [ ] Posting to `/api/experiments/:id/frames/batch` stores frames to SQLite.
- [ ] `GET /api/experiments/:id/frames?limit=100` returns the last 100 frames as JSON.
- [ ] `POST /api/experiments/:id/command` sends a serial command and returns ACK/NAK.
- [ ] `POST /api/experiments/:id/test-connection` pings Arduino and returns result.
- [ ] Finishing or aborting an experiment stops the poller and saves `endedAt`.
- [ ] Deleting an experiment removes it and all its frames (CASCADE).
- [ ] SQLite database file is created automatically at the path specified by `SQLITE_PATH`.
- [ ] `scripts/pi_collector.js` runs standalone via `node scripts/pi_collector.js` with env variables.

---

## 16. Notes & Caveats

- The Arduino **resets when the serial port is opened** (due to DTR/RTS). This is why we keep
  one persistent connection open instead of opening/closing per request.
- The original Django project uses SQLite with table `part_1_experement` (typo intended for
  backward-compat). In the new Express project, use standard Sequelize table names:
  `Experiments` and `Frames`.
- The `second` field in a Frame stores time **in seconds** (float), offset from experiment start.
  The Arduino reports time in milliseconds (`t_ms`), so divide by 1000.
- Pressure (`dif_pressure`) is stored as a raw float. Units are defined by the Arduino sketch
  (typically Pascals or relative pressure).
- The live chart on the detail page should use **Chart.js** line charts with at least 3 series:
  temperature, differential pressure, and RPM.
