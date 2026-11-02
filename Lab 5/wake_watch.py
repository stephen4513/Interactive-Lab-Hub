"""What Woke Me Up? Audio version of Lab 5's overnight/morning pipeline.

Top screen button: start a night / mark a wake-up. Bottom: stop and replay.
Run: python wake_watch.py
Stop the Lab 2 piscreen.service first: its buttons may shut down/reboot the Pi.
Use --terminal for a keyboard-only test; --review reopens the latest night.
Audio stays in wake_nights/. This samples sound, so it can miss short noises.
"""
import argparse
import array
import datetime as dt
import json
import math
from pathlib import Path
import queue
import statistics
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
import uuid
import wave

RATE = 16000


def timestamp(epoch=None):
    return dt.datetime.fromtimestamp(time.time() if epoch is None else epoch).astimezone().isoformat(timespec="milliseconds")


def level_dbfs(pcm):
    """Loudest 100 ms RMS block; relative digital level, not calibrated SPL."""
    values = array.array("h")
    values.frombytes(pcm[:len(pcm) // 2 * 2])
    if sys.byteorder != "little":
        values.byteswap()
    block = RATE // 10
    rms = max((math.sqrt(sum(x*x for x in values[i:i+block]) / len(values[i:i+block]))
               for i in range(0, len(values), block)), default=0)
    return 20 * math.log10(max(rms, 1) / 32768)


def record_sample(device, seconds):
    result = subprocess.run(
        ["arecord", "-q", "-D", device, "-t", "raw", "-f", "S16_LE",
         "-r", str(RATE), "-c", "1", "-d", str(seconds)],
        capture_output=True, timeout=seconds + 5)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    if len(result.stdout) < RATE * seconds * 2 * 0.9:
        raise RuntimeError("The microphone returned an incomplete sample.")
    return result.stdout


def save_wav(path, pcm):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(pcm)


class Night:
    def __init__(self, root, settings):
        self.path = root / (dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
        self.path.mkdir(parents=True, mode=0o700)
        (self.path / "clips").mkdir(mode=0o700)
        self.lock = threading.Lock()
        self.handle = (self.path / "night_log.jsonl").open("a", encoding="utf-8")
        self.write("start", **settings)

    def write(self, kind, **data):
        entry = {"type": kind, "epoch": time.time(), "t": timestamp(), **data}
        with self.lock:
            self.handle.write(json.dumps(entry) + "\n")
            self.handle.flush()
        return entry

    def close(self):
        with self.lock:
            self.handle.close()


def sample_night(night, args, stop, messages):
    baseline = []
    threshold = args.threshold
    clips = failures = 0
    try:
        while not stop.is_set():
            started = time.time()
            tick = time.monotonic()
            try:
                pcm = record_sample(args.device, args.seconds)
                ended = time.time()
                if stop.is_set():
                    break
                level = level_dbfs(pcm)
                failures = 0
                if threshold is None:
                    baseline.append(level)
                    night.write("calibration_sample", dbfs=round(level, 1))
                    if len(baseline) == 3:
                        threshold = min(-1, max(-50, statistics.median(baseline) + 12))
                        night.write("ready", threshold_dbfs=round(threshold, 1))
                        messages.put(("ready", f"Threshold {threshold:.1f} dBFS"))
                else:
                    loud = level >= threshold
                    clip = None
                    if loud and clips < 2000:
                        clip = f"clips/{clips:04d}.wav"
                        save_wav(night.path / clip, pcm)
                        clips += 1
                    night.write("sample", start=started, end=ended,
                                dbfs=round(level, 1), loud=loud, clip=clip)
                    if loud:
                        messages.put(("noise", f"{timestamp(ended)[11:19]}  {level:.1f} dBFS" +
                                      (" saved" if clip else " (clip limit reached)")))
                if args.threshold is not None and not baseline:
                    baseline.append(level)
                    messages.put(("ready", f"Threshold {threshold:.1f} dBFS"))
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                failures += 1
                night.write("error", message=str(error))
                messages.put(("error", str(error)))
                if failures >= 3:
                    messages.put(("fatal", "Microphone failed three times. Monitoring stopped."))
                    break
            stop.wait(max(0, args.interval - (time.monotonic() - tick)))
    except Exception as error:
        # Disk errors must be visible, rather than leaving a false 'listening' screen.
        messages.put(("fatal", f"Recording stopped: {error}"))


def read_night(path):
    records = []
    with (path / "night_log.jsonl").open(encoding="utf-8") as source:
        for number, line in enumerate(source, 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"Skipped incomplete log line {number}.", flush=True)
    return records


def make_report(path, max_gap=120):
    rows = read_night(path)
    samples = [r for r in rows if r["type"] == "sample"]
    marks = [r for r in rows if r["type"] == "wake"]
    loud = [r for r in samples if r["loud"]]
    saved = [r for r in loud if r.get("clip") and (path / r["clip"]).is_file()]
    matches = []
    for mark in marks:
        # Exclude the button tap itself and later noises from possible explanations.
        candidates = [r for r in saved if 0.5 <= mark["epoch"] - r["end"] <= max_gap]
        nearest = max(candidates, key=lambda r: r["end"], default=None)
        matches.append({"wake": mark["t"], "clip": nearest["clip"] if nearest else None,
                        "clip_time": timestamp(nearest["start"]) if nearest else None,
                        "seconds_before": round(mark["epoch"] - nearest["end"], 1) if nearest else None})
    report = {"session": path.name, "started": rows[0]["t"] if rows else None,
              "ended": rows[-1]["t"] if rows else None,
              "sample_count": len(samples), "loud_samples": len(loud), "wake_presses": len(marks),
              "recording_errors": sum(r["type"] == "error" for r in rows), "matches": matches,
              "limitation": "Short audio samples have gaps. A nearby noise is a possible clue, not proof of what woke you."}
    # If no wake-ups were marked, offer the loudest saved sample, clearly labelled.
    if not marks and saved:
        loudest = max(saved, key=lambda r: r["dbfs"])
        report["loudest_clip"] = loudest["clip"]
        report["loudest_time"] = timestamp(loudest["start"])
    (path / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def report_lines(report):
    lines = [f"{report['sample_count']} sound checks; {report['loud_samples']} loud samples; "
             f"{report['wake_presses']} wake-up button presses."]
    for match in report["matches"]:
        at = match["wake"][11:19]
        if match["clip"]:
            lines.append(f"Wake-up mark {at}: saved clip {match['seconds_before']} seconds before the press.")
        else:
            lines.append(f"Wake-up mark {at}: no saved sound matched in the preceding two minutes.")
    if not report["wake_presses"]:
        lines.append("No wake-up button presses were recorded.")
    if not report["loud_samples"]:
        lines.append("No sounds crossed the threshold during the recorded checks.")
    if report["recording_errors"]:
        lines.append(f"There were {report['recording_errors']} microphone recording errors.")
    return lines


def ai_report(path, report, model, messages):
    facts = "\n".join(report_lines(report))
    prompt = ("Write a short, plain two-sentence morning report from ONLY these facts. "
              "You have no audio and cannot name sound sources. Do not claim what caused waking "
              "or that someone slept well. Measurements are sampled, so sounds can be missed.\n" + facts)
    try:
        request = urllib.request.Request("http://localhost:11434/api/generate",
            data=json.dumps({"model": model, "prompt": prompt, "think": False, "stream": False,
                             "options": {"temperature": 0.1, "num_predict": 100}}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            answer = json.load(response)["response"].strip()
        (path / "qwen_report.txt").write_text(answer, encoding="utf-8")
        messages.put(("ai", "Qwen draft (check against the log): " + answer))
    except Exception as error:
        messages.put(("ai", f"Qwen unavailable ({error}). The factual report and clips are still saved."))


class Screen:
    """Same Mini PiTFT SPI setup as the user's Lab 2 screen_boot_script.py."""
    def __init__(self, swap=False):
        import board
        import digitalio
        from PIL import Image, ImageDraw, ImageFont
        from adafruit_rgb_display import st7789
        self.Image, self.ImageDraw = Image, ImageDraw
        self.pins = []
        def pin(name):
            result = digitalio.DigitalInOut(getattr(board, name))
            self.pins.append(result)
            return result
        self.top, self.bottom = pin("D23"), pin("D24")
        for button in (self.top, self.bottom):
            button.switch_to_input(pull=digitalio.Pull.UP)
        if swap:
            self.top, self.bottom = self.bottom, self.top
        self.backlight = pin("D22")
        self.backlight.switch_to_output(value=True)
        cs, dc = pin("D5"), pin("D25")
        self.spi = board.SPI()
        self.display = st7789.ST7789(self.spi, cs=cs, dc=dc, rst=None, baudrate=24000000,
            width=135, height=240, x_offset=53, y_offset=40)
        self.font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        self.previous = [False, False]
        self.last_press = [0.0, 0.0]

    def show(self, text):
        self.backlight.value = True
        image = self.Image.new("RGB", (240, 135), "black")
        draw = self.ImageDraw.Draw(image)
        lines = []
        for line in text.splitlines():
            lines.extend(textwrap.wrap(line, width=27) or [""])
        for i, line in enumerate(lines[:7]):
            draw.text((5, 4 + i * 18), line, font=self.font, fill="#a0b8ad")
        self.display.image(image, 90)

    def dark(self):
        self.backlight.value = False

    def poll(self):
        for i, button in enumerate((self.top, self.bottom)):
            pressed = not button.value
            if pressed and not self.previous[i] and time.monotonic() - self.last_press[i] > 0.3:
                self.last_press[i] = time.monotonic()
                self.previous[i] = pressed
                return "top" if i == 0 else "bottom"
            self.previous[i] = pressed
        return None

    def close(self):
        self.backlight.value = False
        for pin in reversed(self.pins):
            pin.deinit()
        self.spi.deinit()


def keyboard_commands(commands):
    while True:
        try:
            value = input().strip().lower()
        except EOFError:
            return
        commands.put("quit" if value == "q" else "bottom" if value == "s" else "top")


def latest_night(root):
    candidates = [p for p in root.iterdir() if p.is_dir() and (p / "night_log.jsonl").exists()] if root.exists() else []
    if not candidates:
        raise RuntimeError("No recorded nights yet.")
    return max(candidates, key=lambda p: (p / "night_log.jsonl").stat().st_mtime)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="plughw:CARD=w1080p,DEV=0")
    parser.add_argument("--playback-device", help="Optional ALSA output; otherwise use the working aplay default")
    parser.add_argument("--interval", type=float, default=5)
    parser.add_argument("--seconds", type=int, default=2)
    parser.add_argument("--threshold", type=float, help="Override auto-calibration with a dBFS threshold, e.g. -30")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent / "wake_nights")
    parser.add_argument("--terminal", action="store_true", help="Use Enter and s instead of screen buttons")
    parser.add_argument("--swap-buttons", action="store_true")
    parser.add_argument("--review", action="store_true", help="Review the latest saved night")
    parser.add_argument("--no-ai", action="store_true", help="Skip Qwen for a quick hardware test")
    parser.add_argument("--model", default="qwen3.5:0.8b")
    args = parser.parse_args()
    if args.seconds < 1 or args.interval < max(5, args.seconds) or args.interval > 30:
        parser.error("Use interval 5..30 seconds, at least as long as the sample duration.")
    if args.threshold is not None and not -90 <= args.threshold <= 0:
        parser.error("The threshold must be between -90 and 0 dBFS.")
    if not args.terminal:
        check = subprocess.run(["systemctl", "is-active", "--quiet", "piscreen.service"], check=False)
        if check.returncode == 0:
            raise RuntimeError("Run sudo systemctl stop piscreen.service first. Its screen buttons can shut down/reboot the Pi.")
    args.root = args.root.resolve()
    screen = None if args.terminal else Screen(args.swap_buttons)
    commands, messages = queue.Queue(), queue.Queue()
    if args.terminal:
        threading.Thread(target=keyboard_commands, args=(commands,), daemon=True).start()
        print("Enter = top button (start/mark). s + Enter = bottom (stop/replay). q + Enter = quit.", flush=True)
    night = worker = None
    stop = threading.Event()
    active = False
    replay = []
    next_clip = 0
    dark_at = float("inf")

    def show(text):
        print(text.replace("\n", " | "), flush=True)
        if screen:
            screen.show(text)

    def play(path):
        command = ["aplay", "-q"]
        if args.playback_device:
            command += ["-D", args.playback_device]
        result = subprocess.run(command + [str(path)], capture_output=True, timeout=15)
        if result.returncode:
            show("Playback failed.\nCheck audio output.\nClips are saved.")
            print(result.stderr.decode(errors="replace"), flush=True)

    def review(path):
        nonlocal replay, next_clip
        report = make_report(path)
        print("\n".join(report_lines(report)), flush=True)
        print(report["limitation"], flush=True)
        replay = [path / m["clip"] for m in reversed(report["matches"]) if m["clip"]]
        if not report["matches"] and report.get("loudest_clip"):
            replay = [path / report["loudest_clip"]]
        next_clip = 0
        show(f"Morning report\n{report['wake_presses']} wake-up marks\n{report['loud_samples']} loud samples\n"
             + ("Playing saved clip\nBottom: replay/next" if replay else "No matching clip saved") + "\nTop: new night")
        if replay:
            play(replay[0])
        if not args.no_ai:
            threading.Thread(target=ai_report, args=(path, report, args.model, messages), daemon=True).start()

    try:
        if args.review:
            review(latest_night(args.root))
        else:
            show("What Woke Me Up?\nTop: start night\nBottom: last report")
        while True:
            command = screen.poll() if screen else None
            try:
                command = commands.get_nowait() or command
            except queue.Empty:
                pass
            if command == "quit":
                break
            if command == "top":
                if active:
                    mark = night.write("wake")
                    show("Wake-up time saved\n" + mark["t"][11:19] + "\nStill checking sounds")
                    dark_at = time.monotonic() + 3
                else:
                    night = Night(args.root, {"device": args.device, "interval": args.interval, "seconds": args.seconds})
                    stop = threading.Event()
                    active = True
                    show("Night started\nKeep the room quiet\nCalibrating microphone")
                    dark_at = time.monotonic() + 3
                    worker = threading.Thread(target=sample_night, args=(night, args, stop, messages), daemon=True)
                    worker.start()
                    print("Saving to " + str(night.path), flush=True)
            if command == "bottom":
                dark_at = float("inf")
                if active:
                    show("Stopping recording...")
                    stop.set()
                    worker.join(timeout=args.seconds + 7)
                    if worker.is_alive():
                        raise RuntimeError("Microphone worker did not stop.")
                    night.write("stop")
                    night.close()
                    active = False
                    review(night.path)
                elif replay:
                    play(replay[next_clip % len(replay)])
                    next_clip += 1
                else:
                    try:
                        review(latest_night(args.root))
                    except RuntimeError as error:
                        show(str(error) + "\nTop: start night")
            try:
                kind, message = messages.get_nowait()
                print(kind + ": " + message, flush=True)
                if kind == "fatal" and active:
                    stop.set()
                    worker.join(timeout=args.seconds + 7)
                    if not worker.is_alive():
                        night.write("stopped_after_error", message=message)
                        night.close()
                        active = False
                    show("Recording error\nSee terminal\nPress Ctrl+C")
                    dark_at = float("inf")
                elif kind == "ready" and active:
                    show("Ready / listening\nTop: mark wake-up\nBottom: stop + report\nScreen goes dark")
                    dark_at = time.monotonic() + 3
            except queue.Empty:
                pass
            if active and screen and time.monotonic() >= dark_at:
                screen.dark()
                dark_at = float("inf")
            time.sleep(0.04)
    except KeyboardInterrupt:
        print("Stopping. Saved data stays in wake_nights/.", flush=True)
    finally:
        stop.set()
        if worker:
            worker.join(timeout=args.seconds + 7)
        if active and night and (not worker or not worker.is_alive()):
            night.write("stop")
            night.close()
        if screen:
            screen.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"Could not continue: {error}") from error
