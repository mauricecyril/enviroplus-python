#!/usr/bin/env python

import requests
import ST7735
try:
    # Transitional fix for breaking change in LTR559
    from ltr559 import LTR559
    ltr559 = LTR559()
except ImportError:
    import ltr559
    
import time
import colorsys
import os
import sys
from bme280 import BME280
from pms5003 import PMS5003, ReadTimeoutError
from enviroplus import gas
from subprocess import PIPE, Popen, check_output
from PIL import Image, ImageDraw, ImageFont
import logging

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus

print("""luftdaten.py - Reads temperature, pressure, humidity,
PM2.5, and PM10 from Enviro plus and sends data to Luftdaten,
the citizen science air quality project.

Note: you'll need to register with Luftdaten at:
https://meine.luftdaten.info/ and enter your Raspberry Pi
serial number that's displayed on the Enviro plus LCD along
with the other details before the data appears on the
Luftdaten map.

Press Ctrl+C to exit!

""")

bus = SMBus(1)

# Create BME280 instance
bme280 = BME280(i2c_dev=bus)

# Create LCD instance
st7735 = ST7735.ST7735(
    port=0,
    cs=1,
    dc=9,
    backlight=12,
    rotation=270,
    spi_speed_hz=10000000
)

# Logging information
logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)-8s %(message)s',
    level=logging.INFO,
    datefmt='%Y-%m-%d %H:%M:%S')


# Initialize display
st7735.begin()

# Width and height to calculate text position
WIDTH = st7735.width
HEIGHT = st7735.height

# Set up canvas and font
img = Image.new('RGB', (WIDTH, HEIGHT), color=(0, 0, 0))
draw = ImageDraw.Draw(img)
path = os.path.dirname(os.path.realpath(__file__))
font = ImageFont.truetype(path + "/fonts/Asap/Asap-Bold.ttf", 20)
smallfont = ImageFont.truetype(path + "/fonts/Asap/Asap-Bold.ttf", 10)
x_offset = 2
y_offset = 2

message = ""

# The position of the top bar
top_pos = 25

# Create a values dict to store the data
variables = ["temperature",
             "pressure",
             "humidity",
             "light",
             "oxidised",
             "reduced",
             "nh3",
             "pm1",
             "pm25",
             "pm10"]

units = ["C",
         "hPa",
         "%",
         "Lux",
         "kO",
         "kO",
         "kO",
         "ug/m3",
         "ug/m3",
         "ug/m3"]

# Define your own warning limits
# The limits definition follows the order of the variables array
# Example limits explanation for temperature:
# [4,18,28,35] means
# [-273.15 .. 4] -> Dangerously Low
# (4 .. 18]      -> Low
# (18 .. 28]     -> Normal
# (28 .. 35]     -> High
# (35 .. MAX]    -> Dangerously High
# DISCLAIMER: The limits provided here are just examples and come
# with NO WARRANTY. The authors of this example code claim
# NO RESPONSIBILITY if reliance on the following values or this
# code in general leads to ANY DAMAGES or DEATH.
limits = [[4,18,28,35],
          [250,650,1013.25,1015],
          [20,30,60,70],
          [-1,-1,30000,100000],
          [-1,-1,40,50],
          [-1,-1,450,550],
          [-1,-1,200,300],
          [-1,-1,50,100],
          [-1,-1,50,100],
          [-1,-1,50,100]]

# RGB palette for values on the combined screen
palette = [(0,0,255),           # Dangerously Low
           (0,255,255),         # Low
           (0,255,0),           # Normal
           (255,255,0),         # High
           (255,0,0)]           # Dangerously High

screenvalues = {}


# Displays data and text on the 0.96" LCD
def display_text(variable, data, unit):
    # Maintain length of list
    screenvalues[variable] = screenvalues[variable][1:] + [data]
    # Scale the values for the variable between 0 and 1
    colours = [(v - min(screenvalues[variable]) + 1) / (max(screenvalues[variable])
               - min(screenvalues[variable]) + 1) for v in screenvalues[variable]]
    # Format the variable name and value
    message = "{}: {:.1f} {}".format(variable[:4], data, unit)
    logging.info(message)
    draw.rectangle((0, 0, WIDTH, HEIGHT), (255, 255, 255))
    for i in range(len(colours)):
        # Convert the values to colours from red to blue
        colour = (1.0 - colours[i]) * 0.6
        r, g, b = [int(x * 255.0) for x in colorsys.hsv_to_rgb(colour,
                   1.0, 1.0)]
        # Draw a 1-pixel wide rectangle of colour
        draw.rectangle((i, top_pos, i+1, HEIGHT), (r, g, b))
        # Draw a line graph in black
        line_y = HEIGHT - (top_pos + (colours[i] * (HEIGHT - top_pos)))\
                 + top_pos
        draw.rectangle((i, line_y, i+1, line_y+1), (0, 0, 0))
    # Write the text at the top in black
    draw.text((0, 0), message, font=font, fill=(0, 0, 0))
    st7735.display(img)


# Create PMS5003 instance
pms5003 = PMS5003()


# Read values from BME280 and PMS5003 and return as dict
def read_values():
    values = {}
    cpu_temp = get_cpu_temperature()
    raw_temp = bme280.get_temperature()
    comp_temp = raw_temp - ((cpu_temp - raw_temp) / comp_factor)
 
    # Store Temp Values for Luftdaten
    values["temperature"] = "{:.2f}".format(comp_temp)

    # Store Pressure Values for Luftdaten
    values["pressure"] = "{:.2f}".format(bme280.get_pressure() * 100)

    # Store Humidity Values for Luftdaten
    values["humidity"] = "{:.2f}".format(bme280.get_humidity())


    try:
        pm_values = pms5003.read()
        
        values["P2"] = str(pm_values.pm_ug_per_m3(2.5))
        values["P1"] = str(pm_values.pm_ug_per_m3(10))

        
        # Get Temp Values and Display on Screen
        cpu_temps = [get_cpu_temperature()] * 5
        cpu_temps = cpu_temps[1:] + [cpu_temp]
        avg_cpu_temp = sum(cpu_temps) / float(len(cpu_temps))
        raw_temp2 = bme280.get_temperature()
        raw_data = raw_temp2 - ((avg_cpu_temp - raw_temp2) / comp_factor)
        #comp_temp2 = raw_temp - ((cpu_temp - raw_temp2) / comp_factor)
        save_data(0, raw_data)
        display_everything()
        
        # Get Pressure Values and Display on Screen
        raw_data = bme280.get_pressure()
        save_data(1, raw_data)
        display_everything()

        # Get Humidity and Display on Screen
        raw_data = bme280.get_humidity()
        save_data(2, raw_data)

        # Get LUX data and Display on Screen
        proximity = ltr559.get_proximity()
        if proximity < 10:
            raw_data = ltr559.get_lux()
        else:
            raw_data = 1
        save_data(3, raw_data)
        display_everything()

        # Get Gas Reading and Display on Screen
        gas_data = gas.read_all()
        save_data(4, gas_data.oxidising / 1000)
        save_data(5, gas_data.reducing / 1000)
        save_data(6, gas_data.nh3 / 1000)
        display_everything()
        try:
            pms_data = pms5003.read()
        except pmsReadTimeoutError:
            logging.warn("Failed to read PMS5003")
        else:
            save_data(7, float(pms_data.pm_ug_per_m3(1.0)))
            save_data(8, float(pms_data.pm_ug_per_m3(2.5)))
            save_data(9, float(pms_data.pm_ug_per_m3(10)))
            display_everything()
   
    except ReadTimeoutError:
        pms5003.reset()
        pm_values = pms5003.read()
        
        values["P2"] = str(pm_values.pm_ug_per_m3(2.5))
        values["P1"] = str(pm_values.pm_ug_per_m3(10))
    return values



# Get CPU temperature to use for compensation
def get_cpu_temperature():
    process = Popen(['vcgencmd', 'measure_temp'], stdout=PIPE, universal_newlines=True)
    output, _error = process.communicate()
    output = output.decode()
    return float(output[output.index('=') + 1:output.rindex("'")])


# Get Raspberry Pi serial number to use as ID
def get_serial_number():
    with open('/proc/cpuinfo', 'r') as f:
        for line in f:
            if line[0:6] == 'Serial':
                return line.split(":")[1].strip()


# Check for Wi-Fi connection
def check_wifi():
    if check_output(['hostname', '-I']):
        return True
    else:
        return False


# Saves the data to be used in the graphs later and prints to the log
def save_data(idx, data):
    variable = variables[idx]
    # Maintain length of list
    screenvalues[variable] = screenvalues[variable][1:] + [data]
    unit = units[idx]
    message = "{}: {:.1f} {}".format(variable[:4], data, unit)
    logging.info(message)



# Displays all the text on the 0.96" LCD
def display_everything():
    draw.rectangle((0, 0, WIDTH, HEIGHT), (0, 0, 0))
    column_count = 2
    row_count = (len(variables)/column_count)
    for i in xrange(len(variables)):
        variable = variables[i]
        data_value = screenvalues[variable][-1]
        unit = units[i]
        x = x_offset + ((WIDTH/column_count) * (i / row_count))
        y = y_offset + ((HEIGHT/row_count) * (i % row_count))
        message = "{}: {:.1f} {}".format(variable[:4], data_value, unit)
        lim = limits[i]
        rgb = palette[0]
        for j in xrange(len(lim)):
            if data_value > lim[j]:
                rgb = palette[j+1]
        draw.text((x, y), message, font=smallfont, fill=rgb)
    st7735.display(img)



def send_to_luftdaten(values, id):
    pm_values = dict(i for i in values.items() if i[0].startswith("P"))
    temp_values = dict(i for i in values.items() if not i[0].startswith("P"))

    resp_1 = requests.post("https://api.luftdaten.info/v1/push-sensor-data/",
             json={
                 "software_version": "enviro-plus 0.0.1",
                 "sensordatavalues": [{"value_type": key, "value": val} for
                                      key, val in pm_values.items()]
             },
             headers={
                 "X-PIN":    "1",
                 "X-Sensor": id,
                 "Content-Type": "application/json",
                 "cache-control": "no-cache"
             }
    )

    resp_2 = requests.post("https://api.luftdaten.info/v1/push-sensor-data/",
             json={
                 "software_version": "enviro-plus 0.0.1",
                 "sensordatavalues": [{"value_type": key, "value": val} for
                                      key, val in temp_values.items()]
             },
             headers={
                 "X-PIN":    "11",
                 "X-Sensor": id,
                 "Content-Type": "application/json",
                 "cache-control": "no-cache"
             }
    )

    if resp_1.ok and resp_2.ok:
        return True
    else:
        return False


# Compensation factor for temperature
comp_factor = 1.2

# Tuning factor for compensation. Decrease this number to adjust the
# temperature down, and increase to adjust up
#factor = 1.2



# Raspberry Pi ID to send to Luftdaten
id = "raspi-" + get_serial_number()


# Display Raspberry Pi serial and Wi-Fi status
print("Raspberry Pi serial: {}".format(get_serial_number()))
print("Wi-Fi: {}\n".format("connected" if check_wifi() else "disconnected"))

time_since_update = 0
update_time = time.time()

for v in variables:
    screenvalues[v] = [1] * WIDTH

# Main loop to read data, display, and send to Luftdaten
while True:
    try:
        time_since_update = time.time() - update_time
        values = read_values()
        print(values)
        
        if time_since_update > 145:
            resp = send_to_luftdaten(values, id)
            update_time = time.time()
            print("Response: {}\n".format("ok" if resp else "failed"))

    except Exception as e:
        print(e)
