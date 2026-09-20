"""Time to Leave - Stephen's Lab 2 PiClock.

Top button (GPIO23): show all five classes / return to the clock.
Bottom button (GPIO24): start or stop the looping 40-second demo.

Run on the Pi: python time_to_leave.py
Optional: python time_to_leave.py --demo

Schedule repeats weekly, including holidays. DEMO uses simulated time only;
it never changes the Pi's clock. Edit WALK_MINUTES to match your walk.
Display setup follows the course's screen_clock.py and screen_test.py.
Animation, schedule, and button logic were developed with ChatGPT assistance.
"""

import argparse
import math
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw


# ---- Settings you can change ----
WALK_MINUTES = 10
PACKING_WINDOW_MINUTES = 60  # Backpack fills during the hour before leaving.
DEMO_LOOP_SECONDS = 40
TOP_BUTTON_GPIO = 23
BOTTOM_BUTTON_GPIO = 24
TIMEZONE = ZoneInfo("America/New_York")

# Monday = 0, Wednesday = 2, Friday = 4. Times use the 24-hour clock.
# (name on screen, name in list, weekdays, start time, end time)
CLASSES = [
    ("Machine Learning", "ML", (0, 2), (7, 30), (8, 45)),
    ("DSP", "DSP", (0, 2), (10, 10), (11, 25)),
    ("Computer Arch.", "CompArch", (0, 2), (14, 55), (16, 10)),
    ("Interactive Devices", "Devices", (0, 2), (17, 55), (19, 10)),
    ("Product Studio", "Studio", (4,), (12, 10), (14, 40)),
]

WIDTH, HEIGHT = 240, 135
BG = "#F0E9D8"
INK = "#292921"
MUTED = "#756F60"
RULE = "#CEC4AE"
BAG_BASE = "#DBD0B8"
GREEN = "#33734C"
YELLOW = "#966018"
RED = "#B64232"
BLUE = "#365D78"

# An original 5x7 bitmap alphabet. Drawn at whole pixel scales, with no
# font downloads, antialiasing, or platform-specific font dependencies.
GLYPHS = {
    " ": "00000/00000/00000/00000/00000/00000/00000",
    "A": "01110/10001/10001/11111/10001/10001/10001",
    "B": "11110/10001/10001/11110/10001/10001/11110",
    "C": "01111/10000/10000/10000/10000/10000/01111",
    "D": "11110/10001/10001/10001/10001/10001/11110",
    "E": "11111/10000/10000/11110/10000/10000/11111",
    "F": "11111/10000/10000/11110/10000/10000/10000",
    "G": "01111/10000/10000/10111/10001/10001/01111",
    "H": "10001/10001/10001/11111/10001/10001/10001",
    "I": "11111/00100/00100/00100/00100/00100/11111",
    "J": "00111/00010/00010/00010/10010/10010/01100",
    "K": "10001/10010/10100/11000/10100/10010/10001",
    "L": "10000/10000/10000/10000/10000/10000/11111",
    "M": "10001/11011/10101/10101/10001/10001/10001",
    "N": "10001/11001/11001/10101/10011/10011/10001",
    "O": "01110/10001/10001/10001/10001/10001/01110",
    "P": "11110/10001/10001/11110/10000/10000/10000",
    "Q": "01110/10001/10001/10001/10101/10010/01101",
    "R": "11110/10001/10001/11110/10100/10010/10001",
    "S": "01111/10000/10000/01110/00001/00001/11110",
    "T": "11111/00100/00100/00100/00100/00100/00100",
    "U": "10001/10001/10001/10001/10001/10001/01110",
    "V": "10001/10001/10001/10001/10001/01010/00100",
    "W": "10001/10001/10001/10101/10101/10101/01010",
    "X": "10001/10001/01010/00100/01010/10001/10001",
    "Y": "10001/10001/01010/00100/00100/00100/00100",
    "Z": "11111/00001/00010/00100/01000/10000/11111",
    "0": "01110/10001/10011/10101/11001/10001/01110",
    "1": "00100/01100/00100/00100/00100/00100/01110",
    "2": "01110/10001/00001/00010/00100/01000/11111",
    "3": "11110/00001/00001/01110/00001/00001/11110",
    "4": "00010/00110/01010/10010/11111/00010/00010",
    "5": "11111/10000/10000/11110/00001/00001/11110",
    "6": "01110/10000/10000/11110/10001/10001/01110",
    "7": "11111/00001/00010/00100/01000/01000/01000",
    "8": "01110/10001/10001/01110/10001/10001/01110",
    "9": "01110/10001/10001/01111/00001/00001/01110",
    ":": "00000/00100/00100/00000/00100/00100/00000",
    ".": "00000/00000/00000/00000/00000/00110/00110",
    "-": "00000/00000/00000/11111/00000/00000/00000",
    "/": "00001/00001/00010/00100/01000/10000/10000",
    "!": "00100/00100/00100/00100/00100/00000/00100",
    ">": "10000/01000/00100/00010/00100/01000/10000",
    "?": "01110/10001/00001/00010/00100/00000/00100",
}


def text(draw, position, words, scale=1, color=INK, right=False, max_width=None):
    """Write crisp pixel lettering; shrink long countdowns to fit."""
    words = words.upper()
    while max_width and (len(words) * 6 - 1) * scale > max_width and scale > 1:
        scale -= 1
    width = max(0, len(words) * 6 - 1) * scale
    x, y = position
    if right:
        x -= width
    for char in words:
        rows = GLYPHS.get(char, GLYPHS["?"]).split("/")
        for row, pattern in enumerate(rows):
            for column, pixel in enumerate(pattern):
                if pixel == "1":
                    xx, yy = x + column * scale, y + row * scale
                    draw.rectangle((xx, yy, xx + scale - 1, yy + scale - 1), fill=color)
        x += 6 * scale


def seconds_between(later, earlier):
    # Timestamps keep elapsed time correct across daylight-saving changes.
    return later.timestamp() - earlier.timestamp()


def duration(seconds):
    """Round up, so 30 seconds remaining does not say zero minutes."""
    minutes = max(0, math.ceil(seconds / 60))
    days, rest = divmod(minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes} min"


def next_sessions(now):
    """One current or next occurrence of each course, in time order."""
    sessions = []
    for name, short, weekdays, start_time, end_time in CLASSES:
        for offset in range(8):
            day = now + timedelta(days=offset)
            if day.weekday() not in weekdays:
                continue
            start = day.replace(hour=start_time[0], minute=start_time[1],
                                second=0, microsecond=0)
            end = day.replace(hour=end_time[0], minute=end_time[1],
                              second=0, microsecond=0)
            if end > now:
                sessions.append({"name": name, "short": short,
                                 "start": start, "end": end})
                break
    return sorted(sessions, key=lambda session: session["start"])


def class_state(session, now):
    """Decide which animation, color, and countdown to show."""
    start, end = session["start"], session["end"]
    if start <= now < end:
        return "IN CLASS", BLUE, 1.0, seconds_between(end, now)

    leave = start - timedelta(minutes=WALK_MINUTES)
    seconds_to_leave = seconds_between(leave, now)
    fill = min(1.0, max(0.0, 1 - seconds_to_leave / (PACKING_WINDOW_MINUTES * 60)))
    if seconds_to_leave <= 0:
        return "HEAD OUT!", RED, fill, seconds_between(start, now)
    if seconds_to_leave <= 15 * 60:
        return "PACK UP", YELLOW, fill, seconds_between(start, now)
    return "PLENTY OF TIME", GREEN, fill, seconds_between(start, now)


def demo_now(elapsed):
    """A Monday morning: fill the backpack, walk to DSP, then attend it."""
    phase = elapsed % DEMO_LOOP_SECONDS
    # Hold the opening scene, approach departure, walk, then hold IN CLASS.
    if phase < 4:
        minutes = 0
    elif phase < 24:
        minutes = (phase - 4) / 20 * 60
    elif phase < 34:
        minutes = 60 + (phase - 24)
    else:
        minutes = 70 + (phase - 34) / 3
    return datetime(2026, 9, 21, 9, 0, tzinfo=TIMEZONE) + timedelta(minutes=minutes)


def backpack(image, amount, color, elapsed):
    """A little pixel backpack, filled from the bottom as departure nears."""
    sprite = Image.new("RGBA", (32, 34))
    draw = ImageDraw.Draw(sprite)
    draw.rectangle((3, 12, 8, 27), fill=BAG_BASE, outline=INK)
    draw.rectangle((24, 12, 29, 27), fill=BAG_BASE, outline=INK)
    draw.rectangle((12, 2, 21, 8), fill=INK)
    draw.rectangle((14, 4, 19, 7), fill=BG)
    body = [(11, 6), (22, 6), (22, 8), (25, 8), (25, 10),
            (27, 10), (27, 28), (25, 28), (25, 30), (8, 30),
            (8, 28), (6, 28), (6, 10), (8, 10), (8, 8), (11, 8)]
    draw.polygon(body, fill=BAG_BASE)

    mask = Image.new("L", sprite.size)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.polygon(body, fill=255)
    fill_y = 30 - round(amount * 24)
    mask_draw.rectangle((0, 0, 31, fill_y), fill=0)
    sprite.paste(Image.new("RGBA", sprite.size, color), (0, 0), mask)

    draw.line(body + [body[0]], fill=INK, width=1)
    draw.line((9, 14, 24, 14), fill=INK)
    draw.rectangle((21, 14, 22, 17), fill=INK)
    draw.rectangle((10, 20, 23, 26), outline=INK)
    draw.line((12, 22, 21, 22), fill=INK)
    draw.rectangle((10, 10, 11, 12), fill=BG)
    # Discrete movement matches the two-pixel sprite grid.
    bob = 2 if int(elapsed * 2) % 2 else 0
    ImageDraw.Draw(image).rectangle((33, 114, 74, 115), fill=RULE)
    sprite = sprite.resize((64, 68), Image.Resampling.NEAREST)
    image.paste(sprite, (18, 46 - bob), sprite)


def footsteps(image, elapsed):
    """Two chunky shoe prints alternate forward steps."""
    sprite = Image.new("RGBA", (11, 20))
    draw = ImageDraw.Draw(sprite)
    shape = [(3, 1), (7, 1), (9, 3), (9, 9), (8, 11),
             (8, 14), (2, 14), (2, 11), (1, 9), (1, 3)]
    draw.polygon(shape, fill=RED)
    draw.line(shape + [shape[0]], fill=INK)
    draw.line((3, 5, 7, 5), fill=BG)
    draw.line((3, 8, 7, 8), fill=BG)
    draw.rectangle((3, 16, 7, 18), fill=RED, outline=INK)
    frame = int(elapsed * 3) % 4
    for index, x in enumerate((25, 57)):
        lift = (0, -8, 0, 8)[(frame + index * 2) % 4]
        shoe = sprite if index == 0 else sprite.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        shoe = shoe.resize((22, 40), Image.Resampling.NEAREST)
        image.paste(shoe, (x, 65 + lift), shoe)


def open_book(image, elapsed):
    sprite = Image.new("RGBA", (34, 30))
    draw = ImageDraw.Draw(sprite)
    draw.rectangle((2, 5, 31, 25), fill=BLUE, outline=INK)
    left = [(3, 3), (12, 3), (16, 6), (16, 24), (12, 22), (3, 22)]
    right = [(17, 6), (21, 3), (30, 3), (30, 22), (21, 22), (17, 24)]
    for page in (left, right):
        draw.polygon(page, fill=BG)
        draw.line(page + [page[0]], fill=INK)
    for y in (9, 13, 17):
        draw.line((6, y, 12, y), fill=MUTED)
        draw.line((21, y, 27, y), fill=MUTED)
    # A turning page gives the final demo scene a gentle animation.
    page_x = (29, 25, 20, 17, 20, 25)[int(elapsed * 3) % 6]
    draw.line((17, 6, page_x, 3, page_x, 21, 17, 24), fill=BLUE)
    sprite = sprite.resize((68, 60), Image.Resampling.NEAREST)
    image.paste(sprite, (17, 53), sprite)


def render(now, elapsed, show_classes=False, demo=False):
    """Build one frame. This also works on a computer without Pi hardware."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    sessions = next_sessions(now)
    session = sessions[0]
    names = {"ML": "ML", "DSP": "DSP", "CompArch": "COMP ARCH",
             "Devices": "DEVICES", "Studio": "STUDIO"}
    title = "CLASSES" if show_classes else names[session["short"]]
    text(draw, (10, 9), title, scale=2)
    label = ("DEMO " if demo else "") + now.strftime("%H:%M")
    text(draw, (230, 13), label, right=True)
    draw.line((10, 29, 229, 29), fill=INK)

    if show_classes:
        text(draw, (14, 35), "CLASS", color=MUTED)
        text(draw, (94, 35), "STARTS", color=MUTED)
        text(draw, (230, 35), "IN", color=MUTED, right=True)
        for index, session in enumerate(sessions):
            y = 50 + index * 14
            active = session["start"] <= now < session["end"]
            color = BLUE if active else INK
            draw.line((14, y + 10, 229, y + 10), fill=RULE)
            if index == 0:
                text(draw, (4, y), ">", color=BLUE if active else GREEN)
            text(draw, (14, y), names[session["short"]], color=color)
            text(draw, (94, y), session["start"].strftime("%a %H:%M"))
            remaining = ("IN CLASS" if active else
                         duration(seconds_between(session["start"], now)))
            text(draw, (230, y), remaining, color=color, right=True)
    else:
        state, color, amount, seconds = class_state(session, now)
        time_range = session["start"].strftime("%a %H:%M") + " - " + session["end"].strftime("%H:%M")
        text(draw, (10, 36), time_range, color=MUTED)

        if state == "HEAD OUT!":
            footsteps(image, elapsed)
        elif state == "IN CLASS":
            open_book(image, elapsed)
        else:
            backpack(image, amount, color, elapsed)

        minutes = max(0, math.ceil(seconds / 60))
        number = str(minutes) if minutes < 60 else duration(seconds)
        caption = "LEFT IN CLASS" if state == "IN CLASS" else "TO CLASS"
        if minutes < 60:
            caption = "MIN " + caption
        text(draw, (101, 55), number, scale=4, max_width=129)
        text(draw, (102, 87), caption, color=MUTED)
        draw.rectangle((102, 104, 107, 109), fill=color)
        status = "NO RUSH" if state == "PLENTY OF TIME" else state
        text(draw, (114, 104), status, color=color)

    draw.line((10, 122, 229, 122), fill=INK)
    text(draw, (10, 127), "TOP BACK" if show_classes else "TOP LIST", color=MUTED)
    text(draw, (230, 127), "BOTTOM LIVE" if demo else "BOTTOM DEMO",
         color=MUTED, right=True)
    return image


class Button:
    """One event per press: holding or contact bounce will not keep toggling."""
    def __init__(self, pin):
        self.pin = pin
        self.raw = bool(pin.value)
        self.stable = self.raw
        self.changed_at = time.monotonic()

    def pressed(self, now):
        value = bool(self.pin.value)
        if value != self.raw:
            self.raw = value
            self.changed_at = now
        if now - self.changed_at >= 0.035 and value != self.stable:
            self.stable = value
            return not value  # Pull-ups: pressed = LOW.
        return False


def main(start_demo=False):
    # Import hardware libraries only when actually running on the Pi.
    import board
    import digitalio
    import adafruit_rgb_display.st7789 as st7789

    pins = []
    spi = None
    backlight = None
    try:
        def gpio_pin(number):
            pin = digitalio.DigitalInOut(getattr(board, f"D{number}"))
            pins.append(pin)
            return pin

        cs_pin = gpio_pin(5)
        dc_pin = gpio_pin(25)
        spi = board.SPI()
        display = st7789.ST7789(
            spi, cs=cs_pin, dc=dc_pin, rst=None, baudrate=64000000,
            width=135, height=240, x_offset=53, y_offset=40,
        )
        backlight = gpio_pin(22)
        backlight.switch_to_output(value=True)
        top_pin = gpio_pin(TOP_BUTTON_GPIO)
        bottom_pin = gpio_pin(BOTTOM_BUTTON_GPIO)
        top_pin.switch_to_input(pull=digitalio.Pull.UP)
        bottom_pin.switch_to_input(pull=digitalio.Pull.UP)
        top_button, bottom_button = Button(top_pin), Button(bottom_pin)

        demo = start_demo
        show_classes = False
        demo_started = time.monotonic()
        animation_started = demo_started
        next_frame = 0.0
        print("Top: all classes / clock. Bottom: demo / live. Ctrl+C: stop.")
        print(f"Walking time: {WALK_MINUTES} minutes. Demo repeats every 40 seconds.")

        while True:
            tick = time.monotonic()
            top_pressed = top_button.pressed(tick)
            bottom_pressed = bottom_button.pressed(tick)
            if top_pressed:
                show_classes = not show_classes
                next_frame = 0
            if bottom_pressed:
                demo = not demo
                demo_started = tick
                show_classes = False
                next_frame = 0

            if tick >= next_frame:
                now = demo_now(tick - demo_started) if demo else datetime.now(TIMEZONE)
                image = render(now, tick - animation_started, show_classes, demo)
                display.image(image, 90)
                next_frame = tick + 1 / 12  # Animate at about 12 frames/second.
            time.sleep(0.005)
    except KeyboardInterrupt:
        print("\nClock stopped.")
    finally:
        # Release GPIO resources so another screen script can start afterward.
        if backlight is not None:
            try:
                backlight.value = False
            except (RuntimeError, ValueError):
                pass
        for pin in reversed(pins):
            pin.deinit()
        if spi is not None:
            spi.deinit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="start in recording demo mode")
    args = parser.parse_args()
    main(args.demo)
