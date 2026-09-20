import time
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import digitalio
import board
from PIL import Image, ImageDraw, ImageFont
import adafruit_rgb_display.st7789 as st7789


# Set up the display.
cs_pin = digitalio.DigitalInOut(board.D5)
dc_pin = digitalio.DigitalInOut(board.D25)

spi = board.SPI()

disp = st7789.ST7789(
    spi,
    cs=cs_pin,
    dc=dc_pin,
    rst=None,
    baudrate=64000000,
    width=135,
    height=240,
    x_offset=53,
    y_offset=40,
)

# Draw in landscape: 240 pixels wide, 135 pixels tall.
width = disp.height
height = disp.width
rotation = 90

image = Image.new("RGB", (width, height))
draw = ImageDraw.Draw(image)

font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
font = ImageFont.truetype(font_path, 18)
large_font = ImageFont.truetype(font_path, 28)

# Turn on the screen's backlight.
backlight = digitalio.DigitalInOut(board.D22)
backlight.switch_to_output()
backlight.value = True

timezone = ZoneInfo("America/New_York")

while True:
    now = datetime.now(timezone)

    # DSP starts Monday and Wednesday at 10:10 AM.
    # weekday(): Monday = 0, Wednesday = 2.
    for days_ahead in range(8):
        next_class = (now + timedelta(days=days_ahead)).replace(
            hour=10, minute=10, second=0, microsecond=0
        )

        if next_class.weekday() in (0, 2) and next_class > now:
            break

    seconds_left = next_class.timestamp() - now.timestamp()
    minutes_left = math.ceil(seconds_left / 60)

    # Clear the image.
    draw.rectangle((0, 0, width, height), fill="black")

    # Draw the class name, countdown, and start time.
    draw.text((10, 8), "Next class: DSP", font=font, fill="white")
    draw.text(
        (10, 42),
        f"{minutes_left} min",
        font=large_font,
        fill="yellow",
    )
    draw.text(
        (10, 90),
        next_class.strftime("%a 10:10 AM"),
        font=font,
        fill="white",
    )

    disp.image(image, rotation)
    time.sleep(1)