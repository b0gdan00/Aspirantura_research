#include <ctype.h>
#include <max6675.h>

// =========================================================
// asp_experiment_controller
//
// Purpose:
// - Controlled from Raspberry Pi over Serial (line-based protocol).
// - Provides:
//   - START: arm ignition (MOSFET) sequence
//   - STOP: cancel ignition, force MOSFET OFF
//   - PING: connection test
//   - READ_RPM: tachometer reading
//   - READ_PT: pressure + thermocouple reading
//   - READ_ALL: combined reading (recommended for logging)
//
// Protocol (one command per line, case-insensitive):
// - PING          -> "OK PONG"
// - START         -> "OK START"
// - STOP          -> "OK STOP"
// - READ_RPM      -> "OK RPM <t_ms> <rpm>"
// - READ_PT       -> "OK PT  <t_ms> <pressure_pa> <temp_c>"
// - READ_ALL      -> "OK DATA <t_ms> <rpm> <pressure_pa> <temp_c> <mosfet>"
// Unknown         -> "ERR UNKNOWN"
//
// Notes:
// - The Arduino does NOT stream by itself; it responds to requests.
// - t_ms is milliseconds since last START (or since boot if START never
// happened).
// =========================================================

// --- MOSFET (igniter / load) ---
const byte MOSFET_PIN = 8;
const unsigned long MOSFET_DELAY_MS =
    10000; // Delay before turning ON after START
const unsigned long MOSFET_ON_TIME_MS = 5000; // ON duration

// --- Hall sensor (tachometer) ---
const byte HALL_PIN = 3;
const byte PULSES_PER_REV = 2;
const unsigned long RPM_INTERVAL_MS = 500;

// --- MPX5010DP differential pressure sensor ---
const byte PRESSURE_PIN = A0;
const float ADC_REF_VOLTAGE = 5.0;
const float PRESSURE_SENSITIVITY = 0.45f; // V/kPa for MPX5010 (5.0V * 0.09)
const unsigned long PRESSURE_INTERVAL_MS = 50;
const float PRESSURE_FILTER_ALPHA = 0.2f; // Smoother readings (0.0 to 1.0)

// --- Pressure calibration (auto-zero at startup) ---
const int CALIB_SAMPLES = 50;
const unsigned long CALIB_DELAY_US = 2000; // 2 ms between calibration samples
float pressureZeroVoltage = 0.14f;         // will be overwritten by calibration

// --- K-type thermocouple via MAX6675 ---
const byte TC_SO = 4;
const byte TC_CS = 5;
const byte TC_SCK = 6;
// MAX6675 updates internally roughly every ~220ms; reading too fast often
// returns stale values.
const unsigned long TC_INTERVAL_MS = 250;

MAX6675 thermocouple(TC_SCK, TC_CS, TC_SO);

// Tachometer state (interrupt-driven pulse counter)
volatile unsigned long hallPulseCount = 0;
volatile unsigned long lastPulseMicros = 0;

// For computing RPM on demand
unsigned long lastRPMTime = 0;
unsigned long lastPulseSnapshot = 0;
unsigned long currentRPM = 0;

// Physical values (updated on demand)
float pressurePa = 0.0;
float temperatureC = 0.0;
unsigned long lastPressureTime = 0;
unsigned long lastThermoTime = 0;

// Experiment timing
unsigned long experimentT0 = 0; // millis()

// MOSFET control
bool mosfetArmed = false;
bool mosfetEnabled = false;
bool mosfetDone = false;

// Serial line buffer
static const int CMD_BUF_SIZE = 64;
char cmdBuf[CMD_BUF_SIZE];
int cmdLen = 0;

void hallISR() {
  unsigned long now = micros();
  // Simple debounce
  if (now - lastPulseMicros > 300) {
    hallPulseCount++;
    lastPulseMicros = now;
  }
}

void initTachometer() {
  pinMode(HALL_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(HALL_PIN), hallISR, FALLING);
}

void updateRPMOnDemand() {
  // Keep original "windowed" tachometer behaviour: compute RPM once per
  // interval.
  unsigned long now = millis();
  if (now - lastRPMTime < RPM_INTERVAL_MS)
    return;

  unsigned long pulses;
  noInterrupts();
  pulses = hallPulseCount;
  interrupts();

  unsigned long delta = pulses - lastPulseSnapshot;
  lastPulseSnapshot = pulses;

  // delta pulses in RPM_INTERVAL_MS => rpm = (delta / PPR) * (60000 /
  // interval_ms)
  currentRPM = (unsigned long)((delta * 60000UL) /
                               (unsigned long)PULSES_PER_REV / RPM_INTERVAL_MS);
  lastRPMTime = now;
}

void calibratePressureSensor() {
  // Read the sensor N times and average to find the zero-pressure voltage.
  long sum = 0;
  for (int i = 0; i < CALIB_SAMPLES; i++) {
    sum += analogRead(PRESSURE_PIN);
    delayMicroseconds(CALIB_DELAY_US);
  }
  float avgRaw = (float)sum / (float)CALIB_SAMPLES;
  pressureZeroVoltage = avgRaw * (ADC_REF_VOLTAGE / 1023.0f);
}

void updatePressureOnDemand() {
  unsigned long now = millis();
  if (now - lastPressureTime < PRESSURE_INTERVAL_MS)
    return;
  lastPressureTime = now;

  int raw = analogRead(PRESSURE_PIN);
  float voltage = raw * (ADC_REF_VOLTAGE / 1023.0f);

  // Convert to kPa using sensitivity 0.45 V/kPa (for 5V supply)
  float instantKPa = (voltage - pressureZeroVoltage) / PRESSURE_SENSITIVITY;
  float instantPa = instantKPa * 1000.0f;

  // Exponential smoothing filter to reduce noise (low pass filter)
  pressurePa = (instantPa * PRESSURE_FILTER_ALPHA) + (pressurePa * (1.0f - PRESSURE_FILTER_ALPHA));

  if (pressurePa < 0)
    pressurePa = 0;
}

void updateThermocoupleOnDemand() {
  unsigned long now = millis();
  if (now - lastThermoTime < TC_INTERVAL_MS)
    return;
  lastThermoTime = now;
  temperatureC = thermocouple.readCelsius();
}

void forceMosfetOff() {
  digitalWrite(MOSFET_PIN, LOW);
  mosfetEnabled = false;
}

void armExperiment() {
  experimentT0 = millis();
  mosfetArmed = true;
  mosfetDone = false;
  forceMosfetOff();
  // Reset tachometer window baseline for a clean start
  noInterrupts();
  hallPulseCount = 0;
  interrupts();
  lastPulseSnapshot = 0;
  currentRPM = 0;
  lastRPMTime = millis();
}

void stopExperiment() {
  mosfetArmed = false;
  mosfetDone = true;
  forceMosfetOff();
}

void updateMosfet() {
  if (!mosfetArmed || mosfetDone)
    return;

  unsigned long now = millis();
  unsigned long elapsed = now - experimentT0;

  if (!mosfetEnabled && elapsed >= MOSFET_DELAY_MS) {
    digitalWrite(MOSFET_PIN, HIGH);
    mosfetEnabled = true;
  }

  if (mosfetEnabled && elapsed >= (MOSFET_DELAY_MS + MOSFET_ON_TIME_MS)) {
    forceMosfetOff();
    mosfetDone = true;
  }
}

unsigned long tMs() {
  // If START never happened, experimentT0==0 and this is "since boot" which is
  // OK for debugging.
  return millis() - experimentT0;
}

void printOk(const char *msg) {
  Serial.print("OK ");
  Serial.println(msg);
}

bool cmdEqualsIgnoreCase(const char *a, const char *b) {
  while (*a && *b) {
    char ca = (char)tolower((unsigned char)*a);
    char cb = (char)tolower((unsigned char)*b);
    if (ca != cb)
      return false;
    a++;
    b++;
  }
  return *a == '\0' && *b == '\0';
}

void handleCommand(const char *cmd) {
  // Uppercase compare without allocating
  if (cmdEqualsIgnoreCase(cmd, "PING")) {
    printOk("PONG");
    return;
  }

  if (cmdEqualsIgnoreCase(cmd, "CALIBRATE")) {
    calibratePressureSensor();
    Serial.print("OK CALIB ");
    Serial.println(pressureZeroVoltage, 4);
    return;
  }

  if (cmdEqualsIgnoreCase(cmd, "START")) {
    armExperiment();
    printOk("START");
    return;
  }

  if (cmdEqualsIgnoreCase(cmd, "STOP")) {
    stopExperiment();
    printOk("STOP");
    return;
  }

  if (cmdEqualsIgnoreCase(cmd, "READ_RPM")) {
    updateRPMOnDemand();
    Serial.print("OK RPM ");
    Serial.print(tMs());
    Serial.print(" ");
    Serial.println(currentRPM);
    return;
  }

  if (cmdEqualsIgnoreCase(cmd, "READ_PT")) {
    updatePressureOnDemand();
    updateThermocoupleOnDemand();
    Serial.print("OK PT ");
    Serial.print(tMs());
    Serial.print(" ");
    Serial.print(pressurePa, 1);
    Serial.print(" ");
    Serial.println(temperatureC, 3);
    return;
  }

  if (cmdEqualsIgnoreCase(cmd, "READ_ALL")) {
    updateRPMOnDemand();
    updatePressureOnDemand();
    updateThermocoupleOnDemand();
    Serial.print("OK DATA ");
    Serial.print(tMs());
    Serial.print(" ");
    Serial.print(currentRPM);
    Serial.print(" ");
    Serial.print(pressurePa, 1);
    Serial.print(" ");
    Serial.print(temperatureC, 3);
    Serial.print(" ");
    Serial.println(mosfetEnabled ? 1 : 0);
    return;
  }

  Serial.println("ERR UNKNOWN");
}

void pumpSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r')
      continue;
    if (c == '\n') {
      cmdBuf[cmdLen] = '\0';
      if (cmdLen > 0)
        handleCommand(cmdBuf);
      cmdLen = 0;
      continue;
    }

    if (cmdLen < (CMD_BUF_SIZE - 1)) {
      cmdBuf[cmdLen++] = c;
    } else {
      // Overflow: reset buffer and wait for newline
      cmdLen = 0;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(250);

  pinMode(MOSFET_PIN, OUTPUT);
  forceMosfetOff();

  initTachometer();
  pinMode(PRESSURE_PIN, INPUT);

  // Auto-calibrate pressure sensor at startup (assumes zero pressure now)
  calibratePressureSensor();

  // Default time base: "since boot" until START is received.
  experimentT0 = 0;
  lastRPMTime = 0;
  lastPressureTime = 0;
  lastThermoTime = 0;

  Serial.print("OK READY CALIB_V=");
  Serial.println(pressureZeroVoltage, 4);
}

void loop() {
  pumpSerial();
  // Maintain fresh cached sensor values for the next READ_* request.
  updateRPMOnDemand();
  updatePressureOnDemand();
  updateThermocoupleOnDemand();
  updateMosfet();
}
