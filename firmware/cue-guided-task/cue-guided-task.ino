#include "sound.h"

// ---------------- Animal-specific configuration ----------------
#define ANIMAL_ID "RAT001"
// true: high sweep -> left, low sweep -> right
// false: high sweep -> right, low sweep -> left
bool highSweepIsLeft = true;

// ---------------- Pin Assignments ----------------
// Maze sensors (inputs)
const int sensorPins[6] = {2,3,4,5,6,7};    // central entry, central exit, L mid, R mid, L return, R return

// Reward valves (outputs)
const int valvePins[3]  = {22,23,24};       // Left, Right, Start

// TTL outputs from Arduino (to Open Ephys)
const int ttlChoicePin    = 36;
const int ttlReturnArmPin = 38;

// TTL inputs from maze (left/right/start licks)
const int ttlLicks[3] = {30,31,32}; // these go directly to Open Ephys

// ---------------- Session control ----------------
const int MAX_TRIALS = 200;
const unsigned long MAX_TIME = 3600000UL; // 1h in ms
unsigned long startTime;
int trialCount = 0;
bool sessionActive = true;

// ---------------- Trial sequence ----------------
const int BLOCK_SIZE = 20;
const int HALF_BLOCK = 10;
int trialSequence[MAX_TRIALS];
int trialIndex = 0;
int correctArm = -1;
int choiceArm = -1;
bool trialActive = false;
bool sweepsOn = false;

bool startRewardEnabled = false; // optional start port reward

// ---------------- Sensor names for logging ----------------
const char* sensorNames[6] = {
  "CENTRAL_ENTRY","CENTRAL_EXIT",
  "LEFT_CHOICE","RIGHT_CHOICE",
  "LEFT_RETURN","RIGHT_RETURN"
};

// ---------------- Non-blocking reward structure ----------------
struct RewardValve {
  int pin;
  unsigned long startTime;
  unsigned long duration;
  bool active;
};

RewardValve rewardValves[3] = {
  {22,0,100,false},
  {23,0,100,false},
  {24,0,100,false}
};

// ---------------- Non-blocking TTL pulse structure ----------------
struct TTLPulse {
    int pin;
    unsigned long startTime;
    unsigned long duration;
    bool active;
};

// Add choice and return arm TTLs
TTLPulse ttlChoice  = {ttlChoicePin,0,5,false};
TTLPulse ttlReturnArm = {ttlReturnArmPin,0,5,false};

// ---------------- Setup ----------------
void setup() {
  Serial.begin(115200);

  // Sensors input
  for(int i=0;i<6;i++) pinMode(sensorPins[i], INPUT_PULLUP);
  // Valves output
  for(int i=0;i<3;i++) { pinMode(valvePins[i], OUTPUT); digitalWrite(valvePins[i], LOW);}
  
  // TTL outputs
  pinMode(ttlChoice.pin, OUTPUT); digitalWrite(ttlChoice.pin, LOW);
  pinMode(ttlReturnArm.pin, OUTPUT); digitalWrite(ttlReturnArm.pin, LOW);

  pinMode(ttlSweepOn.pin, OUTPUT); digitalWrite(ttlSweepOn.pin, LOW);
  pinMode(ttlSweepOff.pin, OUTPUT); digitalWrite(ttlSweepOff.pin, LOW);

  setupSound();       // DAC initialization
  generateAllBlocks(); // 20-trial pseudo-random blocks
  logTrialSequence();  // send planned trial list over Serial
  startTime = millis();
}

// ---------------- Loop ----------------
void loop() {
  if(!sessionActive) return;

  // Check session timeout
  if(millis()-startTime >= MAX_TIME){
    stopSession();
    return;
  }

  // Start trial on central entry
  if(!trialActive && sensorTriggered(0)){
    if(trialCount >= MAX_TRIALS){ stopSession(); return; }
    startTrial();
  }

  // ---------------- Repeated sweeps until central exit ----------------
  if(trialActive && !sensorTriggered(1)){ // central exit not triggered
    if(canStartNextSweep()){
      if(highSweepIsLeft){
        if(correctArm==0) startSweepAsync(8000,32000,100); // high->left
        else startSweepAsync(4000,16000,100);             // low->right
      } else {
        if(correctArm==0) startSweepAsync(4000,16000,100); // low->left
        else startSweepAsync(8000,32000,100);             // high->right
      }
    }
  }

  // Stop sweep at central exit
  if(trialActive && sensorTriggered(1) && sweepPlaying){
    stopSweepAsync();
  }

  // Choice detection
  if(trialActive && choiceArm == -1){
    if(sensorTriggered(2)) { choiceArm=0; evaluateChoice(); }
    if(sensorTriggered(3)) { choiceArm=1; evaluateChoice(); }
  }

  // Return arm detection
  if(trialActive && (sensorTriggered(4) || sensorTriggered(5))){
    // Start non-blocking TTL pulse
    ttlReturnArm.startTime = millis();
    ttlReturnArm.active = true;
    digitalWrite(ttlReturnArm.pin,HIGH);

    logEvent("RETURN_ARM","ENTRY");

    if(startRewardEnabled) deliverReward(2); // optional start port
    resetTrial();
  }

  // ---------------- Non-blocking TTL update ----------------
  updateTTLPulse(ttlSweepOn);
  updateTTLPulse(ttlSweepOff);
  updateTTLPulse(ttlChoice);
  updateTTLPulse(ttlReturnArm);

  // ---------------- Non-blocking reward update ----------------
  for(int i=0;i<3;i++){
    if(rewardValves[i].active && millis() - rewardValves[i].startTime >= rewardValves[i].duration){
      digitalWrite(rewardValves[i].pin, LOW);
      rewardValves[i].active = false;
    }
  }

  // Log sensor HIGH events
  static bool prevState[6] = {0,0,0,0,0,0};
  for(int i=0;i<6;i++){
    bool curr = sensorTriggered(i);
    if(curr && !prevState[i]) logEvent("SENSOR",sensorNames[i]);
    prevState[i]=curr;
  }
}

// ---------------- Trial functions ----------------
void startTrial(){
  correctArm = trialSequence[trialIndex];
  trialActive = true;
  sweepsOn = true;

  logEvent("TRIAL", correctArm==0?"LEFT":"RIGHT");
}

void evaluateChoice(){
  // Start non-blocking TTL pulse for choice
  ttlChoice.startTime = millis();
  ttlChoice.active = true;
  digitalWrite(ttlChoice.pin,HIGH);

  if(choiceArm==correctArm) deliverReward(choiceArm);
}

// ---------------- Non-blocking reward ----------------
void deliverReward(int port){
  digitalWrite(rewardValves[port].pin, HIGH);
  rewardValves[port].startTime = millis();
  rewardValves[port].active = true;

  logEvent("REWARD", port==0?"LEFT":(port==1?"RIGHT":"START"));
}

void resetTrial(){
  trialActive=false;
  choiceArm=-1;
  trialIndex++;
  trialCount++;
  if(trialIndex>=MAX_TRIALS) stopSession();
}

void stopSession(){
  sessionActive=false;
  stopSweepAsync();
  for(int i=0;i<3;i++) digitalWrite(valvePins[i],LOW);
  Serial.println("SESSION_COMPLETE");
}

// ---------------- Utilities ----------------
bool sensorTriggered(int idx){ return digitalRead(sensorPins[idx])==LOW; }

void logEvent(const char* type,const char* detail){
  unsigned long timestamp=millis()-startTime;
  Serial.print(type); Serial.print(","); Serial.print(timestamp); Serial.print(","); Serial.println(detail);
}

// ---------------- Trial sequence ----------------
void generateAllBlocks(){
  for(int block=0; block<MAX_TRIALS/BLOCK_SIZE; block++){
    int offset=block*BLOCK_SIZE;
    for(int i=0;i<HALF_BLOCK;i++){
      trialSequence[offset+i]=0;              // left
      trialSequence[offset+i+HALF_BLOCK]=1;   // right
    }
    // shuffle block
    for(int i=BLOCK_SIZE-1;i>0;i--){
      int j=random(0,i+1);
      int temp=trialSequence[offset+i];
      trialSequence[offset+i]=trialSequence[offset+j];
      trialSequence[offset+j]=temp;
    }
  }
  trialIndex=0;
}

void logTrialSequence(){
  Serial.println("TRIAL_SEQUENCE_START");
  for(int i=0;i<MAX_TRIALS;i++){
    Serial.print(i+1); Serial.print(",");
    Serial.println(trialSequence[i]==0?"LEFT":"RIGHT");
  }
  Serial.println("TRIAL_SEQUENCE_END");
}
