const int AUDIO_PIN = 9; // Connects to Potentiometer Pin 3

void setup() {
  // No initialization needed for tone()
}

void loop() {
  // Play a simple 440Hz tone (A4) for 500 milliseconds                                                                                                              
  tone(AUDIO_PIN, 440);
  delay(3000);
  
  // Play a 880Hz tone (A5) for 500 milliseconds
  //tone(AUDIO_PIN, 880);
  //delay(3000);
  
  // Pause for 1 second
  noTone(AUDIO_PIN);
  delay(3000);
}