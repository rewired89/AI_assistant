# Input test (second firmware of ours)
Three boxes at the top light teal while BOOT/-, PWR, +/KEY are held; a pink dot follows a finger. At boot it logs every I2C address that answers.
```
. ~/esp/esp-idf/export.sh      # in bash
cd ~/Desktop/REPOS/AI_assistant && git pull origin main
cd firmware/input_test
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyACM0 flash monitor     # exit the monitor with Ctrl+]
```
Not compiled or run by the author when written. Touch address 0x15 and the register layout (0x01..0x06) are from the CST816 vendor driver convention and unverified here.
