#include <max6675.h>
#include <ctype.h>

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
// - READ_PT       -> "OK PT  <t_ms> <pressure_kpa> <temp_c>"
// - READ_ALL      -> "OK DATA <t_ms> <rpm> <pressure_kpa> <temp_c> <mosfet>"
// Unknown         -> "ERR UNKNOWN"
//
// Notes:
// - The Arduino does NOT stream by itself; it responds to requests.
// - t_ms is milliseconds since last START (or since boot if START never happened).
// =========================================================

// --- MOSFET (igniter / load) ---
const byte MOSFET_PIN = 8;
const unsigned long MOSFET_DELAY_MS   = 10000; // Delay before turning ON after START
const unsigned long MOSFET_ON_TIME_MS = 5000;  // ON duration

// --- Hall sensor (tachometer) ---
const byte HALL_PIN = 3;
const byte PULSES_PER_REV = 2;

// --- MPX5010DP differential pressure sensor ---
const byte PRESSURE_PIN = A0;
const float ADC_REF_VOLTAGE = 5.0;

// --- K-type thermocouple via MAX6675 ---
const byte TC_SO  = 4;
const byte TC_CS  = 5;
const byte TC_SCK = 6;

MAX6675 thermocouple(TC_SCK, TC_CS, TC_SO);

// Tachometer state (interrupt-driven pulse counter)
volatile unsigned long hallPulseCount = 0;
volatile unsigned long lastPulseMicros = 0;

// For computing RPM on demand
unsigned long lastRpmComputeMicros = 0;
unsigned long lastPulseSnapshot = 0;
unsigned long currentRPM = 0;

// Physical values (updated on demand)
float pressureKPa = 0.0;
float temperatureC = 0.0;

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
  unsigned long now = micros();
  if (lastRpmComputeMicros == 0) {
    lastRpmComputeMicros = now;
    noInterrupts();
    lastPulseSnapshot = hallPulseCount;
    interrupts();
    currentRPM = 0;
    return;
  }

  unsigned long pulses;
  noInterrupts();
  pulses = hallPulseCount;
  interrupts();

  unsigned long deltaPulses = pulses - lastPulseSnapshot;
  unsigned long deltaMicros = now - lastRpmComputeMicros;

  lastPulseSnapshot = pulses;
  lastRpmComputeMicros = now;

  if (deltaMicros == 0) {
    currentRPM = 0;
    return;
  }

  // rpm = (deltaPulses / PULSES_PER_REV) / (deltaMicros / 60e6)
  //     = deltaPulses * 60e6 / (PULSES_PER_REV * deltaMicros)
  unsigned long long num = (unsigned long long)deltaPulses * 60000000ULL;
  unsigned long long den = (unsigned long long)PULSES_PER_REV * (unsigned long long)deltaMicros;
  currentRPM = den ? (unsigned long)(num / den) : 0;
}

void updatePressureOnDemand() {
  int raw = analogRead(PRESSURE_PIN);
  float voltage = raw * (ADC_REF_VOLTAGE / 1023.0);
  // From your original calibration:
  // pressureKPa = (voltage - 0.14) * (10.0 / 4.7)
  pressureKPa = (voltage - 0.14f) * (10.0f / 4.7f);
  if (pressureKPa < 0) pressureKPa = 0;
}

void updateThermocoupleOnDemand() {
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
  // Reset RPM computation baseline for a clean start
  lastRpmComputeMicros = 0;
}

void stopExperiment() {
  mosfetArmed = false;
  mosfetDone = true;
  forceMosfetOff();
}

void updateMosfet() {
  if (!mosfetArmed || mosfetDone) return;

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
  // If START never happened, experimentT0==0 and this is "since boot" which is OK for debugging.
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
    if (ca != cb) return false;
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
    Serial.print(pressureKPa, 3);
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
    Serial.print(pressureKPa, 3);
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
    if (c == '\r') continue;
    if (c == '\n') {
      cmdBuf[cmdLen] = '\0';
      if (cmdLen > 0) handleCommand(cmdBuf);
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

  // Default time base: "since boot" until START is received.
  experimentT0 = 0;

  printOk("READY");
}

void loop() {
  pumpSerial();
  updateMosfet();
}
