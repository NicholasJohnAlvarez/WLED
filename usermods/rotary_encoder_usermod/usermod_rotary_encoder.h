#pragma once

#include "wled.h"
#include <ESP32Encoder.h>
#include <DFRobot_DF1201S.h>

class RotaryEncoderUsermod : public Usermod {
  private:
    // DFPlayer pins and objects
    static const int DFPLAYER_RX = 18;  // ESP32 RX <- DFPlayer TX
    static const int DFPLAYER_TX = 8;   // ESP32 TX -> DFPlayer RX
    HardwareSerial dfSerial = HardwareSerial(2);  // Use UART2 for DFPlayer communication
    DFRobot_DF1201S player;
    bool dfPlayerInitialized = false;
    
    // Audio preset to track mapping
    // Preset 27 -> track 001
    // Preset 9 -> track 002
    // Preset 20 -> tracks 003-014 (plays sequentially through folder)
    static const int NUM_AUDIO_PRESETS = 3;
    struct AudioPreset {
      int presetNumber;
      int trackNumber;
      String filePath;
      bool playMultiple;  // True if this preset should cycle through multiple tracks
      int startTrack;     // Starting track for multi-track presets
      int endTrack;       // Ending track for multi-track presets
    };
    AudioPreset audioPresets[NUM_AUDIO_PRESETS] = {
      {27, 1, "", false, 0, 0},     // Preset 27 plays track 001.mp3
      {9, 2, "", false, 0, 0},      // Preset 9 plays track 002.mp3
      {20, 0, "", true, 3, 14}      // Preset 20 plays tracks 003-014 (cycles through)
    };
    
    // Track which track to play next for preset 20 (cycles 3-14)
    int preset20CurrentTrack = 3;

    // Pin definitions
    static const int PIN_A = 4;        // Encoder pin A (change to your GPIO)
    static const int PIN_B = 5;        // Encoder pin B (change to your GPIO)
    static const int PIN_SW = 6;       // Encoder button (change to your GPIO)
    static const int PIN_VOLUME_POT = 9;  // ADC pin for volume potentiometer (ADC1_8, RGB LED will glow)
    
    // Encoder object
    ESP32Encoder encoder;
    
    // Button debouncing
    unsigned long lastButtonPress = 0;
    const unsigned long debounceDelay = 200;
    bool lastButtonState = HIGH;
    bool buttonPressed = false;
    
    // Encoder variables
    int64_t lastEncoderValue = 0;
    int currentPreset = 1;
    const int totalPresets = 27;
    
    // Brightness states
    const byte brightnessOn = 50;
    bool isOn = true;
    
    // Update intervals
    unsigned long lastEncoderCheck = 0;
    const unsigned long encoderCheckInterval = 50;
    
    // Volume control variables
    unsigned long lastVolumeCheck = 0;
    const unsigned long volumeCheckInterval = 100;  // Check volume every 100ms
    int lastVolume = -1;  // Track last volume to avoid unnecessary updates
    const int volumeHysteresis = 2;  // Minimum change needed to update volume (reduces jitter)
    const int defaultVolume = 20;  // Default startup volume (0-30)

  public:
    void setup() {
      // Configure button pin
      pinMode(PIN_SW, INPUT_PULLUP);
      
      // Configure ADC pin for volume potentiometer
      pinMode(PIN_VOLUME_POT, INPUT);
      
      // Setup encoder
      ESP32Encoder::useInternalWeakPullResistors = UP;
      encoder.attachHalfQuad(PIN_A, PIN_B);
      encoder.clearCount();
      encoder.setCount(0);
      lastEncoderValue = 0;
      
      Serial.println(F("Rotary Encoder Usermod initialized"));
      // Start with WLED off
      bri = 0;
      isOn = false;
      stateUpdated(CALL_MODE_INIT);

      // Initialize DFPlayer
      dfSerial.begin(115200, SERIAL_8N1, DFPLAYER_RX, DFPLAYER_TX);
      delay(2000);  // Give DFPlayer time to boot
      
      if (player.begin(dfSerial)) {
        Serial.println(F("DFPlayer initialized successfully!"));
        player.setVol(defaultVolume);  // Set initial volume to default
        lastVolume = defaultVolume;  // Initialize tracking variable
        player.switchFunction(player.MUSIC);
        player.setPrompt(false);  // Disable voice prompts
        dfPlayerInitialized = true;
      } else {
        Serial.println(F("DFPlayer init failed - check wiring"));
        dfPlayerInitialized = false;
      }
    }

    void loop() {
      unsigned long now = millis();
      
      // Check button state
      handleButton(now);
      
      // Check encoder rotation (throttled to avoid excessive updates)
      if (now - lastEncoderCheck >= encoderCheckInterval) {
        lastEncoderCheck = now;
        handleEncoder();
      }
      
      // Check volume potentiometer (throttled to avoid excessive updates)
      if (now - lastVolumeCheck >= volumeCheckInterval) {
        lastVolumeCheck = now;
        handleVolumePot();
      }
    }
    
    void handleButton(unsigned long now) {
      bool buttonState = digitalRead(PIN_SW);
      
      // Detect button press (transition from HIGH to LOW)
      if (buttonState == LOW && lastButtonState == HIGH) {
        if (now - lastButtonPress > debounceDelay) {
          lastButtonPress = now;
          toggleOnOff();
        }
      }
      
      lastButtonState = buttonState;
    }
    
    void handleEncoder() {
      int64_t currentValue = encoder.getCount() / 2;
      int64_t delta = currentValue - lastEncoderValue;
  
      if (delta != 0) {
        lastEncoderValue = currentValue;
        
        // Update preset based on rotation direction
        if (delta > 0) {
          // Clockwise - next preset
          currentPreset++;
          if (currentPreset > totalPresets) {
            currentPreset = 1;
          }
        } else {
          // Counter-clockwise - previous preset
          currentPreset--;
          if (currentPreset < 1) {
            currentPreset = totalPresets;
          }
        }
        
        // Apply the preset
        applyPreset(currentPreset);
        
        // Check if this preset should trigger audio
        playAudioForPreset(currentPreset);
        
        Serial.print(F("Preset changed to: "));
        Serial.println(currentPreset);
      }
    }
    
    void playAudioForPreset(int preset) {
      if (!dfPlayerInitialized) {
        return;  // Don't try to play if DFPlayer isn't initialized
      }
      
      // Search for matching preset in audio presets array
      for (int i = 0; i < NUM_AUDIO_PRESETS; i++) {
        if (audioPresets[i].presetNumber == preset && audioPresets[i].presetNumber != 0) {
          // Found a match! Play the corresponding track
          
          // Re-read and apply volume from pot before playing to ensure it's set
          int adcValue = analogRead(PIN_VOLUME_POT);
          int currentVolume = map(adcValue, 0, 4095, 0, 30);
          
          // If pot is at minimum (volume 0), use default volume instead
          if (currentVolume < 3) {
            currentVolume = defaultVolume;
          }
          
          player.setVol(currentVolume);
          lastVolume = currentVolume;
          
          delay(50);  // Brief delay to ensure volume command is processed
          player.setPlayMode(player.SINGLE);  // Play once and stop
          
          int trackToPlay = 0;
          
          // Check if this preset plays multiple tracks (like preset 20)
          if (audioPresets[i].playMultiple) {
            // Use the cycling track number for preset 20
            if (preset == 20) {
              trackToPlay = preset20CurrentTrack;
              
              // Cycle to next track for next time
              preset20CurrentTrack++;
              if (preset20CurrentTrack > audioPresets[i].endTrack) {
                preset20CurrentTrack = audioPresets[i].startTrack;  // Wrap back to track 3
              }
            }
            
            Serial.print(F("Playing track "));
            Serial.print(trackToPlay);
            Serial.print(F(" (00"));
            Serial.print(trackToPlay);
            Serial.print(F(".mp3) [Next: "));
            Serial.print(preset20CurrentTrack);
            Serial.println(F("]"));
          } else {
            // Single track preset (27 or 9)
            trackToPlay = audioPresets[i].trackNumber;
            
            Serial.print(F("Playing track "));
            Serial.print(trackToPlay);
            Serial.print(F(" (00"));
            Serial.print(trackToPlay);
            Serial.println(F(".mp3)"));
          }
          
          // Check if we should play by file path or by number
          if (audioPresets[i].filePath.length() > 0) {
            // Play by file path (for renamed files)
            player.playSpecFile(audioPresets[i].filePath);
          } else {
            // Play by track number
            player.playFileNum(trackToPlay);
          }
          
          break;  // Exit loop after finding match
        }
      }
    }
    
    void handleVolumePot() {
      if (!dfPlayerInitialized) {
        return;  // Don't try to adjust volume if DFPlayer isn't initialized
      }
      
      // Read ADC value (12-bit on ESP32: 0-4095)
      int adcValue = analogRead(PIN_VOLUME_POT);
      
      // Map ADC value to DFPlayer volume range (0-30)
      // ESP32 ADC: 0 = 0V, 4095 = 3.3V
      int newVolume = map(adcValue, 0, 4095, 0, 30);
      
      // Apply hysteresis to avoid jitter from ADC noise
      if (lastVolume == -1 || abs(newVolume - lastVolume) >= volumeHysteresis) {
        lastVolume = newVolume;
        player.setVol(newVolume);
        
        Serial.print(F("Volume set to: "));
        Serial.print(newVolume);
        Serial.print(F(" (ADC: "));
        Serial.print(adcValue);
        Serial.println(F(")"));
      }
    }
    
    void toggleOnOff() {
      if (isOn) {
        // Turn off - set brightness to 0
        bri = 0;
        isOn = false;
        Serial.println(F("WLED turned OFF"));
      } else {
        // Turn on - set brightness to 50
        bri = brightnessOn;
        isOn = true;
        Serial.println(F("WLED turned ON"));
      }
      
      stateUpdated(CALL_MODE_BUTTON);
      updateInterfaces(CALL_MODE_BUTTON);
    }

    uint16_t getId() {
      return USERMOD_ID_ROTARY_PRESET;
    }
};

static RotaryEncoderUsermod rotaryEncoderUsermod;
REGISTER_USERMOD(rotaryEncoderUsermod);