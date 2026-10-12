# Audio test (third firmware of ours)
+/KEY plays a soft 1 s tone. BOOT/- records 3 s and prints a mic level bar. PWR (short press) plays the recording back.
```
. ~/esp/esp-idf/export.sh
cd ~/Desktop/REPOS/AI_assistant && git pull origin main
cd firmware/audio_test
idf.py set-target esp32s3
idf.py build          # downloads espressif/esp_codec_dev the first time (needs internet)
idf.py -p /dev/ttyACM0 flash monitor
```
Volume is capped (OUT_VOLUME_MAX 90 of 100; the tone ramps 50, 65, 80, 90). Not compiled or run by the author when written. Codec setup follows the vendor demo; speaker loudness and mic gain are unmeasured.
