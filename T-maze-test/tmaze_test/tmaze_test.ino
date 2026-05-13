// ================= PIN SETUP =================

// Body sensors (6)
const int sensorPins[6] = {38, 39, 40, 41, 42, 43};
// 38 = left, 39 = center, 40 = right, 41 = right choice,
// 42 = center end, 43 = left choice
// Lick sensors (3)
const int lickPins[3] = {35, 36, 37};

// Solenoids
const int solenoidPins[3][5] = {
  {23, 24, 25, 26, 44}, // left
  {27, 28, 29, 30, 45}, // right
  {31, 32, 33, 34, 46}  // center
};

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

  CONSOLE.println("READY");
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

      CONSOLE.print("S,");
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

      CONSOLE.print("L,");
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

    CONSOLE.print("SOL,");
    CONSOLE.print(port);
    CONSOLE.print(",");
    CONSOLE.println(sol);

    digitalWrite(solenoidPins[port][sol], HIGH);
    delay(pulseTime);
    digitalWrite(solenoidPins[port][sol], LOW);
  }
}