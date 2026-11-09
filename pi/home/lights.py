import RPi.GPIO as gpio
from time import *

gpio.setmode(gpio.BCM)
gpio.setwarnings(False)
gpio.setup(16, gpio.OUT)
gpio.setup(20, gpio.OUT)
gpio.setup(21, gpio.OUT)

while True:
    gpio.output(16, gpio.HIGH)
    gpio.output(20, gpio.LOW)
    gpio.output(21, gpio.LOW)
    sleep(1)

    gpio.output(20, gpio.HIGH)
    sleep(1)

    gpio.output(16, gpio.LOW)
    gpio.output(20, gpio.LOW)
    gpio.output(21, gpio.HIGH)
    sleep(1)

    gpio.output(20, gpio.HIGH)
    gpio.output(21, gpio.LOW)
    sleep(1)

    gpio.output(16, gpio.HIGH)
    gpio.output(20, gpio.LOW)
    sleep(1)
