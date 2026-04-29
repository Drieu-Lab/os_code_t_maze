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
  {32, 33, 34, 31, 46}  // center
};

int lastSensor[6];
int lastLick[3];

const int pulseTime = 150;

// ================= SETUP =================

void setup() {
  Serial.begin(115200);
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

  Serial.println("READY");
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

      Serial.print("S,");
      Serial.print(i);
      Serial.print(",");
      Serial.println(state);
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

      Serial.print("L,");
      Serial.print(i);
      Serial.print(",");
      Serial.println(state);
    }
  }
}

// ================= SERIAL =================

void handleSerial() {
  if (!Serial.available()) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();

  if (cmd.length() == 3 && cmd[0] == 'R') {
    int port = cmd[1] - '0';
    int sol  = cmd[2] - '0';

    if (port < 0 || port > 2 || sol < 0 || sol > 4) return;

    Serial.print("SOL,");
    Serial.print(port);
    Serial.print(",");
    Serial.println(sol);

    digitalWrite(solenoidPins[port][sol], HIGH);
    delay(pulseTime);
    digitalWrite(solenoidPins[port][sol], LOW);
  }
}