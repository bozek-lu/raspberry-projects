#!/bin/bash

# pins setup
gpio -g mode 21 out # buzzer
gpio -g mode 12 pwm # LED
gpio -g mode 26 up # button

# led turn on
gpio -g pwm 12 800
gpio -g wfi 26 falling

# led off, buzzer on
gpio -g pwm 12 100
gpio -g write 21 0

rpicam-still -n --datetime

# buzzer off
gpio -g write 21 1
gpio -g pwm 12 0
