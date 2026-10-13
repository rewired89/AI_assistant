# Power probe
Prints the battery-sense ADC and the charging flag once a second. About 3.0 to 4.2 V on the estimate = a lithium cell is connected; about 0 V or a wandering value = no battery.
```
. ~/esp/esp-idf/export.sh
cd ~/Desktop/REPOS/AI_assistant && git pull origin main
cd firmware/power_probe
idf.py set-target esp32s3 && idf.py build && idf.py -p /dev/ttyACM0 flash monitor
```
Not compiled or run by the author when written. Pin meanings are from vendor source.
