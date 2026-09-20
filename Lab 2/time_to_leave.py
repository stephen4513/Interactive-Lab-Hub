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
from functools import lru_cache
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont


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
BG = "#080E18"
PANEL = "#152332"
WHITE = "#F3F7FC"
MUTED = "#A6B7C8"
GREEN = "#62DCA0"
YELLOW = "#FFD36B"
RED = "#FF7B80"
BLUE = "#7BC6FF"


@lru_cache(maxsize=24)
def get_font(size, bold=False):
    suffix = "-Bold" if bold else ""
    path = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf"
    return ImageFont.truetype(path, size)


def text(draw, position, words, size=12, color=WHITE, bold=False,
         anchor="lt", max_width=None):
    """Keep text inside its assigned part of this very small screen."""
    font = get_font(size, bold)
    while max_width and draw.textlength(words, font=font) > max_width and size > 9:
        size -= 1
        font = get_font(size, bold)
    draw.text(position, words, font=font, fill=color, anchor=anchor)


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
    """Draw and fill a rounded backpack; clip the fill to its actual shape."""
    bob = round(math.sin(elapsed * 3) * 1.5)
    left, top, right, bottom = 26, 64 + bob, 77, 111 + bob
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, top + 12, 83, bottom - 6), radius=7,
                           outline=MUTED, width=2)
    draw.rounded_rectangle((41, top - 7, 62, top + 6), radius=5,
                           outline=WHITE, width=2)
    draw.rounded_rectangle((left, top, right, bottom), radius=11, fill=PANEL)

    mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((left + 2, top + 2, right - 2, bottom - 2),
                                radius=9, fill=255)
    fill_y = bottom - 1 - round((bottom - top - 3) * amount)
    mask_draw.rectangle((left, top, right, fill_y), fill=0)
    image.paste(Image.new("RGB", image.size, color), (0, 0), mask)

    draw.rounded_rectangle((left, top, right, bottom), radius=11,
                           outline=WHITE, width=2)
    draw.line((left + 7, top + 14, right - 7, top + 14), fill=BG, width=2)
    draw.rounded_rectangle((left + 9, top + 25, right - 9, bottom - 6),
                           radius=4, outline=BG, width=2)
    draw.line((right - 9, top + 13, right - 9, top + 19), fill=WHITE, width=2)


def footsteps(draw, elapsed):
    """Alternating footprints travel upward while it is time to leave."""
    for index in range(2):
        progress = (elapsed * 0.55 + index / 2) % 1.0
        x = 28 if index % 2 == 0 else 57
        y = round(87 - progress * 26)
        shade = tuple(round(channel * (1 - 0.65 * progress)) for channel in (255, 123, 128))
        draw.ellipse((x, y, x + 11, y + 17), fill=shade)
        draw.ellipse((x + 2, y + 18, x + 9, y + 23), fill=shade)
    draw.line((17, 113, 80, 113), fill=MUTED)


def open_book(draw, elapsed):
    draw.polygon([(21, 68), (48, 73), (48, 108), (21, 103)],
                 fill=PANEL, outline=BLUE)
    draw.polygon([(48, 73), (77, 68), (77, 103), (48, 108)],
                 fill=PANEL, outline=BLUE)
    for y in (80, 87, 94):
        draw.line((27, y, 42, y + 3), fill=MUTED)
        draw.line((55, y + 3, 70, y), fill=MUTED)
    # A small moving page glint keeps the final scene visibly animated.
    x = 50 + round((math.sin(elapsed * 2) + 1) * 11)
    draw.line((x, 76, x, 98), fill=WHITE)


def render(now, elapsed, show_classes=False, demo=False):
    """Build one frame. This also works on a computer without Pi hardware."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    sessions = next_sessions(now)
    text(draw, (8, 5), "ALL CLASSES" if show_classes else "TIME TO LEAVE",
         size=11, bold=True)
    label = ("DEMO " if demo else "") + now.strftime("%H:%M")
    text(draw, (232, 5), label, size=11, color=YELLOW if demo else MUTED, anchor="rt")
    draw.line((8, 21, 231, 21), fill=PANEL)

    if show_classes:
        text(draw, (8, 25), "COURSE", size=9, color=MUTED)
        text(draw, (92, 25), "NEXT START", size=9, color=MUTED)
        text(draw, (232, 25), "TIME LEFT", size=9, color=MUTED, anchor="rt")
        for index, session in enumerate(sessions):
            y = 40 + index * 15
            active = session["start"] <= now < session["end"]
            color = BLUE if active else (GREEN if index == 0 else WHITE)
            if index == 0:
                draw.rounded_rectangle((5, y - 2, 234, y + 12), radius=3, fill=PANEL)
            text(draw, (8, y), session["short"], size=11, color=color)
            text(draw, (92, y), session["start"].strftime("%a %H:%M"), size=10)
            remaining = ("IN CLASS" if active else
                         duration(seconds_between(session["start"], now)))
            text(draw, (232, y), remaining, size=10, color=color, anchor="rt")
    else:
        session = sessions[0]
        state, color, amount, seconds = class_state(session, now)
        text(draw, (8, 26), session["name"], size=16, bold=True, max_width=224)
        time_range = session["start"].strftime("%a %H:%M") + " - " + session["end"].strftime("%H:%M")
        text(draw, (8, 46), time_range, size=10, color=MUTED)

        if state == "HEAD OUT!":
            footsteps(draw, elapsed)
        elif state == "IN CLASS":
            open_book(draw, elapsed)
        else:
            backpack(image, amount, color, elapsed)

        text(draw, (97, 62), duration(seconds), size=26, color=color,
             bold=True, max_width=137)
        text(draw, (99, 89), "until class ends" if state == "IN CLASS" else "until class",
             size=10, color=MUTED)
        text(draw, (99, 104), state, size=11, color=color, bold=True, max_width=135)

    draw.line((8, 119, 231, 119), fill=PANEL)
    top_hint = "TOP: CLOCK" if show_classes else "TOP: CLASSES"
    text(draw, (8, 124), top_hint, size=9, color=MUTED)
    text(draw, (232, 124), "BOTTOM: LIVE" if demo else "BOTTOM: DEMO",
         size=9, color=YELLOW if demo else MUTED, anchor="rt")
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
