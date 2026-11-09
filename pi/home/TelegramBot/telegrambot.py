#coder :- Salman Faris

import os
import sys
import time
import telepot
import RPi.GPIO as GPIO
import random
from glob import glob

#LED
def on(pin):
        GPIO.output(16, GPIO.HIGH)
        GPIO.output(20, GPIO.HIGH)
        return "is on"
def off(pin):
        GPIO.output(16, GPIO.LOW)
        GPIO.output(20, GPIO.LOW)
        return "is off"
# to use Raspberry Pi board pin numbers
GPIO.setmode(GPIO.BCM)
# set up GPIO output channel
GPIO.setup(16, GPIO.OUT)
GPIO.setup(20, GPIO.OUT)

def handle(msg):
    chat_id = msg['chat']['id']
    command = msg['text'].lower()

    print(msg)
    print(chat_id)
    print('Got command: %s' % command)

    if command == 'on':
        bot.sendMessage(chat_id, on(16))
    elif command =='off':
        bot.sendMessage(chat_id, off(16))
    elif command == 'pandorka':
        sendingRandomPandoraPic(chat_id)
         

bot = telepot.Bot(os.environ['OLD_TELEGRAM_BOT_TOKEN'])
bot.message_loop(handle)
print('I am listening...')

def sendingRandomPandoraPic(chat_id):
    file_path_types = ["./pandorka/*.jpg", "./pandorka/*.heic"]
    pic_list = glob("./pandorka/*")

    print(pic_list)

    get_random_pic = random.choice(pic_list)
    with open(get_random_pic, 'rb') as photo:
        bot.sendPhoto(chat_id, photo)

while 1:
    try:
        time.sleep(10)
    
    except KeyboardInterrupt:
        print('\n Program interrupted')
        GPIO.cleanup()
        exit()
    
    except:
        print('Other error or exception occured!')
        GPIO.cleanup()
