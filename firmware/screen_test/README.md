# Screen test (first firmware of ours)
Build from bash with ESP-IDF v5.5.1 active (`. ~/esp/esp-idf/export.sh`):
```
cd firmware/screen_test
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```
Expected: screen cycles teal, pink, white, black, once a second, backlight at 30%. Exit the monitor with Ctrl+].
Restore the factory firmware: `esptool --port /dev/ttyACM0 write-flash 0 factory_backup.bin`.
Not compiled or run by the author when written. Pins are from the vendor demo and unverified.
