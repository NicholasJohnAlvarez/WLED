/*
 * RotaryEncoderUsermod.h
 * 
 * SUMMARY:
 * Custom WLED usermod for rotary encoder control with DFPlayer audio integration.
 * Supports preset navigation, volume control via potentiometer, and synchronized
 * GIF/audio playback for LED matrix displays.
 * 
 * FEATURES:
 * - Rotary encoder for preset navigation (1-30)
 * - Button press to toggle display on/off
 * - DFPlayer Mini MP3 playback tied to specific presets
 * - Volume control via potentiometer (ADC)
 * - Stop audio on preset change or display off
 * 
 * HARDWARE:
 * - ESP32-S3 with PSRAM
 * - Rotary encoder (pins 4, 5, 6)
 * - DFPlayer Mini (UART2: RX=18, TX=8)
 * - Volume potentiometer (ADC pin 9)
 * - WS2812B LED matrix (32x48)
 * 
 * CHANGELOG:
 * -----------------------------------------------------------------------------
 * 2025-01-02 | v1.1.0 | Audio control update
 *            | - Added stopAudio() called on preset change and display off
 *            | - Only stop audio when leaving an audio preset (not scrolling through)
 *            | - Fixed track skipping on rapid preset changes for preset 20
 * -----------------------------------------------------------------------------
 * 2024-12-23 | v1.0.0 | Initial release
 *            | - Rotary encoder preset navigation (30 presets)
 *            | - DFPlayer integration with preset-to-track mapping
 *            | - Volume potentiometer with hysteresis
 *            | - Preset 20 cycles through tracks 3-14
 * -----------------------------------------------------------------------------
 */

#pragma once

#include "wled.h"
#include <ESP32Encoder.h>
#include <DFRobot_DF1201S.h>

class RotaryEncoderUsermod : public Usermod {
  private:
    // DFPlayer pins and objects
    static const int DFPLAYER_RX = 18;
    static const int DFPLAYER_TX = 8;
    HardwareSerial dfSerial = HardwareSerial(2);
    DFRobot_DF1201S player;
    bool dfPlayerInitialized = false;
    bool audioPlaying = false;  // Track if audio is currently playing
    
    // Audio preset to track mapping
    // Preset 27 -> track 001.mp3
    // Preset 9  -> track 002.mp3
    // Preset 20 -> track 003.mp3
    static const int NUM_AUDIO_PRESETS = 3;
    struct AudioPreset {
      int presetNumber;
      int trackNumber;
    };
    AudioPreset audioPresets[NUM_AUDIO_PRESETS] = {
      {27, 1},
      {9, 2},
      {20, 3}
    };
    
    // Track which preset started the current audio (0 = none)
    int audioPresetActive = 0;

    // Pin definitions
    static const int PIN_A = 4;
    static const int PIN_B = 5;
    static const int PIN_SW = 6;
    static const int PIN_VOLUME_POT = 9;
    
    ESP32Encoder encoder;
    
    unsigned long lastButtonPress = 0;
    const unsigned long debounceDelay = 200;
    bool lastButtonState = HIGH;
    bool buttonPressed = false;
    
    int64_t lastEncoderValue = 0;
    int currentPreset = 1;
    const int totalPresets = 30;
    
    const byte brightnessOn = 50;
    bool isOn = true;
    bool wasOn = true;
    
    unsigned long lastEncoderCheck = 0;
    const unsigned long encoderCheckInterval = 50;
    
    unsigned long lastVolumeCheck = 0;
    const unsigned long volumeCheckInterval = 100;
    int lastVolume = -1;
    const int volumeHysteresis = 2;
    const int defaultVolume = 20;

  public:
    void setup() {
      pinMode(PIN_SW, INPUT_PULLUP);
      pinMode(PIN_VOLUME_POT, INPUT);
      
      ESP32Encoder::useInternalWeakPullResistors = UP;
      encoder.attachHalfQuad(PIN_A, PIN_B);
      encoder.clearCount();
      encoder.setCount(0);
      lastEncoderValue = 0;
      
      Serial.println(F("Rotary Encoder Usermod initialized"));
      bri = 0;
      isOn = false;
      wasOn = false;
      stateUpdated(CALL_MODE_INIT);

      dfSerial.begin(115200, SERIAL_8N1, DFPLAYER_RX, DFPLAYER_TX);
      delay(2000);
      
      if (player.begin(dfSerial)) {
        Serial.println(F("DFPlayer initialized successfully!"));
        player.setVol(defaultVolume);
        lastVolume = defaultVolume;
        player.switchFunction(player.MUSIC);
        player.setPrompt(false);
        dfPlayerInitialized = true;
      } else {
        Serial.println(F("DFPlayer init failed - check wiring"));
        dfPlayerInitialized = false;
      }
    }

    void loop() {
      unsigned long now = millis();
      
      checkDisplayState();
      handleButton(now);
      
      if (now - lastEncoderCheck >= encoderCheckInterval) {
        lastEncoderCheck = now;
        handleEncoder();
      }
      
      if (now - lastVolumeCheck >= volumeCheckInterval) {
        lastVolumeCheck = now;
        handleVolumePot();
      }
    }
    
    void checkDisplayState() {
      bool currentlyOn = (bri > 0);
      
      if (wasOn && !currentlyOn) {
        stopAudio();
        Serial.println(F("Display turned off - stopping audio"));
      }
      
      wasOn = currentlyOn;
      isOn = currentlyOn;
    }
    
    void stopAudio() {
      if (dfPlayerInitialized && audioPlaying) {
        player.pause();
        audioPlaying = false;
        audioPresetActive = 0;
        Serial.println(F("Audio stopped"));
      }
    }
    
    void playTrack(int trackNumber) {
      if (!dfPlayerInitialized) return;
      
      player.setPlayMode(player.SINGLE);
      player.playFileNum(trackNumber);
      audioPlaying = true;
      
      Serial.print(F("Now playing track: "));
      Serial.println(trackNumber);
    }
    
    void handleButton(unsigned long now) {
      bool buttonState = digitalRead(PIN_SW);
      
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
        
        int previousPreset = currentPreset;
        
        if (delta > 0) {
          currentPreset++;
          if (currentPreset > totalPresets) {
            currentPreset = 1;
          }
        } else {
          currentPreset--;
          if (currentPreset < 1) {
            currentPreset = totalPresets;
          }
        }
        
        // Only stop audio if we're leaving an audio preset that was playing
        if (audioPlaying && audioPresetActive != currentPreset) {
          stopAudio();
        }
        
        applyPreset(currentPreset);
        playAudioForPreset(currentPreset);
        
        Serial.print(F("Preset changed to: "));
        Serial.println(currentPreset);
      }
    }
    
    void playAudioForPreset(int preset) {
      if (!dfPlayerInitialized) {
        return;
      }
      
      // Find matching audio preset
      for (int i = 0; i < NUM_AUDIO_PRESETS; i++) {
        if (audioPresets[i].presetNumber == preset) {
          
          // Set volume from potentiometer
          int adcValue = analogRead(PIN_VOLUME_POT);
          int currentVolume = map(adcValue, 0, 4095, 0, 30);
          
          if (currentVolume < 3) {
            currentVolume = defaultVolume;
          }
          
          player.setVol(currentVolume);
          lastVolume = currentVolume;
          
          delay(50);
          
          // Play the track
          int trackToPlay = audioPresets[i].trackNumber;
          playTrack(trackToPlay);
          audioPresetActive = preset;
          
          Serial.print(F("Preset "));
          Serial.print(preset);
          Serial.print(F(" -> Playing track "));
          Serial.println(trackToPlay);
          
          break;
        }
      }
    }
    
    void handleVolumePot() {
      if (!dfPlayerInitialized) {
        return;
      }
      
      int adcValue = analogRead(PIN_VOLUME_POT);
      int newVolume = map(adcValue, 0, 4095, 0, 30);
      
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
        bri = 0;
        isOn = false;
        stopAudio();
        Serial.println(F("WLED turned OFF"));
      } else {
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