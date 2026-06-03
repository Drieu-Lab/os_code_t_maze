// ================= PIN SETUP =================
// Pins come from rig_config.h, a symlink to the canonical
// ../../common/rig_config.h (see docs/decisions/0006).
#include "rig_config.h"

// Body sensors (6): single source of truth = SENSOR_PINS in rig_config.h.
// Both this sketch and cue-guided-task index S,i the same way (Beam/role order:
// ENTRY, EXIT, L_CHOICE, R_CHOICE, L_RETURN, R_RETURN) so rig-view matches both.
const int sensorPins[6] = SENSOR_PINS;
// Lick sensors (3)
const int lickPins[3] = LICK_PINS;

// Solenoids [port][R1,R2,R3,DRAIN,RINSE]; ports: left, right, center
const int solenoidPins[3][5] = SOLENOID_TABLE;

int lastSensor[6];
int lastLick[3];

const int pulseTime = 150;

// ----- Arduino Due: use Programming port for Serial Monitor (optional) -----
// Native USB (default): the USB socket next to the reset button → SerialUSB → CONSOLE.
// Programming port: the other USB socket (near the power jack) → hardware Serial → CONSOLE.
// To use the programming port: plug into that port, select its COM port in the IDE, then
// uncomment exactly the #define line below. The sketch will use Serial and will not wait
// in while (!SerialUSB) in setup().
//
// #define DUE_USE_NATIVE_USB 0
//
// ---------------------------------------------------------------------------

// Arduino Due: Native USB → SerialUSB; Programming port USB → Serial.
#if defined(__SAM3X8E__)
#ifndef DUE_USE_NATIVE_USB
#define DUE_USE_NATIVE_USB 1
#endif
#if DUE_USE_NATIVE_USB
#define CONSOLE SerialUSB
#else
#define CONSOLE Serial
#endif
#else
#define CONSOLE Serial
#endif

// ================= SETUP =================

void setup() {
  // LED: 3 quick = start of setup; 1 long after READY = setup finished (serial/baud if no text).
  pinMode(LED_BUILTIN, OUTPUT);
  for (int i = 0; i < 3; i++) {
    digitalWrite(LED_BUILTIN, HIGH);
    delay(80);
    digitalWrite(LED_BUILTIN, LOW);
    delay(80);
  }

  CONSOLE.begin(115200);
#if defined(__SAM3X8E__) && DUE_USE_NATIVE_USB
  while (!SerialUSB) {
    delay(10);
  }  // wait until Serial Monitor opens the native USB CDC port
#endif
  delay(2000);  // allow stable serial

  // Sensors (HIGH idle, LOW event)
  for (int i = 0; i < 6; i++) {
    pinMode(sensorPins[i], INPUT_PULLUP);
    lastSensor[i] = 0;
  }

  // Licks (LOW idle, HIGH event)
  for (int i = 0; i < 3; i++) {
    pinMode(lickPins[i], INPUT_PULLUP);
    lastLick[i] = 0;
  }

  // Solenoids
  for (int p = 0; p < 3; p++) {
    for (int s = 0; s < 5; s++) {
      pinMode(solenoidPins[p][s], OUTPUT);
      digitalWrite(solenoidPins[p][s], LOW);
    }
  }

  CONSOLE.println("0,PROTO,2");
  CONSOLE.print(millis()); CONSOLE.println(",READY");
  CONSOLE.flush();

  digitalWrite(LED_BUILTIN, HIGH);
  delay(400);
  digitalWrite(LED_BUILTIN, LOW);
}

// ================= LOOP =================

void loop() {
  updateSensors();
  updateLicks();
  handleSerial();
}

// ================= SENSORS =================

void updateSensors() {
  for (int i = 0; i < 6; i++) {

    int raw = digitalRead(sensorPins[i]);

    // HIGH idle → LOW event → invert
    int state = (raw == LOW) ? 1 : 0;

    if (state != lastSensor[i]) {
      lastSensor[i] = state;

      CONSOLE.print(millis());
      CONSOLE.print(",S,");
      CONSOLE.print(i);
      CONSOLE.print(",");
      CONSOLE.println(state);
    }
  }
}

// ================= LICKS =================

void updateLicks() {
  for (int i = 0; i < 3; i++) {

    int raw = digitalRead(lickPins[i]);

    // LOW idle → HIGH event → keep as is
    int state = (raw == HIGH) ? 1 : 0;

    if (state != lastLick[i]) {
      lastLick[i] = state;

      CONSOLE.print(millis());
      CONSOLE.print(",L,");
      CONSOLE.print(i);
      CONSOLE.print(",");
      CONSOLE.println(state);
    }
  }
}

// ================= SERIAL =================

void handleSerial() {
  if (!CONSOLE.available()) return;

  String cmd = CONSOLE.readStringUntil('\n');
  cmd.trim();

  if (cmd.length() == 3 && cmd[0] == 'R') {
    int port = cmd[1] - '0';
    int sol  = cmd[2] - '0';

    if (port < 0 || port > 2 || sol < 0 || sol > 4) return;

    CONSOLE.print(millis());
    CONSOLE.print(",SOL,");
    CONSOLE.print(port);
    CONSOLE.print(",");
    CONSOLE.println(sol);

    digitalWrite(solenoidPins[port][sol], HIGH);
    delay(pulseTime);
    digitalWrite(solenoidPins[port][sol], LOW);
  }
}