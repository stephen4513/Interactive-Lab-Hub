#!/usr/bin/env python3
"""Terminal Wizard-of-Oz study assistant. Tap=talk; turn=minutes; hold=timer.
Run in speech-scripts: python study_assistant.py --test-seconds 10
The wizard selects spoken replies using arrow keys/Enter or number keys.
"""
import argparse
import json
import math
import threading
import time
from pathlib import Path


class Button:
    """Debounce presses; a hold never also generates a recording tap."""
    def __init__(self):
        self.raw = self.down = self.held = False
        self.changed = self.pressed = 0.0

    def update(self, pressed, now):
        if pressed != self.raw:
            self.raw, self.changed = pressed, now
        event = None
        if now - self.changed >= 0.04 and self.raw != self.down:
            self.down = self.raw
            if self.down:
                self.pressed, self.held = now, False
            elif not self.held:
                event = 'tap'
        if self.down and not self.held and now - self.pressed >= 1.2:
            self.held = True
            event = 'hold'
        return event


class Session:
    def __init__(self, test_seconds=None):
        self.subject = ''
        self.minutes = 30
        self.mode = 'ready'
        self.remaining = 0
        self.deadline = None
        self.test_seconds = test_seconds

    def turn(self, delta):
        if self.mode == 'ready':
            self.minutes = max(5, min(180, self.minutes + delta * 5))
            return True
        return False

    def left(self, now):
        return max(0, self.deadline - now) if self.mode == 'running' else self.remaining

    def start(self, now):
        self.remaining = self.test_seconds or self.minutes * 60
        self.deadline = now + self.remaining
        self.mode = 'running'

    def hold(self, now):
        if self.mode == 'ready':
            return 'start' if self.subject else 'missing_subject'
        if self.mode == 'running':
            self.remaining = self.left(now)
            self.deadline = None
            self.mode = 'paused'
            return 'pause'
        self.deadline = now + self.remaining
        self.mode = 'running'
        return 'resume'

    def stop(self):
        self.mode, self.remaining, self.deadline = 'ready', 0, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test-seconds', type=float)
    parser.add_argument('--reverse', action='store_true')
    parser.add_argument('--address', type=lambda s: int(s, 0), default=0x36)
    parser.add_argument('--wizard', action='store_true', help='accepted for compatibility; wizard is always enabled')
    args = parser.parse_args()
    if args.test_seconds is not None and (not math.isfinite(args.test_seconds) or not 1 <= args.test_seconds <= 3600):
        parser.error('--test-seconds must be between 1 and 3600')
    import curses
    import board
    import numpy as np
    import sherpa_onnx
    import sounddevice as sd
    from adafruit_seesaw import digitalio, neopixel, rotaryio, seesaw
    from faster_whisper import WhisperModel
    from echo_bot import Speaker, DEFAULT_VOICE, DEFAULT_VAD

    voice = DEFAULT_VOICE if DEFAULT_VOICE.is_file() else Path(__file__).parent / 'en_US-lessac-medium.onnx'
    for path in (voice, DEFAULT_VAD):
        if not path.is_file():
            parser.error(f'Missing {path}. Run the lab setup.sh first.')
    print('Loading encoder and speech models...', flush=True)
    device = seesaw.Seesaw(board.I2C(), addr=args.address)
    device.pin_mode(24, device.INPUT_PULLUP)
    button = digitalio.DigitalIO(device, 24)
    encoder = rotaryio.IncrementalEncoder(device)
    pixel = neopixel.NeoPixel(device, 6, 1)
    pixel.brightness = 0.15
    speaker = Speaker(voice)
    recognizer = WhisperModel('base.en', device='cpu', compute_type='int8')
    log_dir = Path(__file__).resolve().parent.parent / 'part2-recordings'
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f'wizard-{time.time_ns()}.jsonl'
    lock = threading.RLock()
    state = Session(args.test_seconds)
    view = {'busy': False, 'phase': 'Ready', 'heard': '', 'reply': '',
            'note': 'Choose a reply below. Participant: tap the knob to speak.'}
    stopping = threading.Event()
    worker = None

    def log(kind, value):
        with lock, log_file.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'time': time.time(), 'kind': kind, 'value': value}) + '\n')

    def launch(job, phase):
        nonlocal worker
        with lock:
            if view['busy']:
                view['note'] = 'Wait until speech or recording finishes, then try again.'
                return False
            view['busy'], view['phase'] = True, phase
        def run():
            try:
                job()
            except Exception as exc:
                with lock:
                    view['note'] = f'{type(exc).__name__}: {exc}'
                log('error', str(exc))
            finally:
                with lock:
                    view['busy'], view['phase'] = False, 'Ready'
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        return True

    def speak(text, after=None):
        def job():
            with lock:
                view['reply'] = text
            log('device_reply', text)
            speaker.say(text)
            if after and not stopping.is_set():
                with lock:
                    after()
        return launch(job, 'Speaking')

    def listen():
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(DEFAULT_VAD)
        config.silero_vad.min_silence_duration = 0.6
        config.sample_rate = 16000
        vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)
        buffer = np.empty(0, dtype=np.float32)
        window = config.silero_vad.window_size
        log('listening_started', 'knob tap; silence threshold 0.6 seconds')
        started = time.monotonic()
        with sd.InputStream(channels=1, dtype='float32', samplerate=16000) as stream:
            while not stopping.is_set() and time.monotonic() - started < 45:
                chunk, overflow = stream.read(1600)
                if overflow:
                    log('audio_warning', 'input overflow')
                buffer = np.concatenate([buffer, chunk.reshape(-1)])
                while len(buffer) >= window:
                    vad.accept_waveform(buffer[:window])
                    buffer = buffer[window:]
                if not vad.empty():
                    audio = np.array(vad.front.samples, dtype=np.float32)
                    break
            else:
                with lock:
                    view['note'] = 'No answer captured. Tap the knob to try again.'
                return
        with lock:
            view['phase'] = 'Thinking'
        log('turn_ended', len(audio) / 16000)
        segments, _ = recognizer.transcribe(audio, beam_size=1)
        heard = ' '.join(s.text.strip() for s in segments)
        with lock:
            view['heard'] = heard
            view['note'] = 'Wizard: S sets subject, U uses transcript; then choose a reply.' if heard else 'No words recognized. Choose Repeat, then tap to speak again.'
        log('participant_transcript', heard)

    labels = ['Study goal', 'Time + plan', 'Confirm', 'Repeat', 'Reflect', 'Complete', 'Custom']

    def ui(screen):
        curses.curs_set(0)
        screen.nodelay(True)
        screen.keypad(True)
        edges = Button()
        sign = 1 if args.reverse else -1
        position = sign * encoder.position
        selected = 0
        edit = None
        text = ''
        last_color = None
        log('configuration', {'silence': 0.6, 'test_seconds': args.test_seconds, 'controller': 'terminal menu'})

        def draw(row, line, highlight=False):
            height, width = screen.getmaxyx()
            if row < height - 1 and width > 1:
                try:
                    screen.addnstr(row, 0, line, width - 1, curses.A_REVERSE if highlight else 0)
                except curses.error:
                    pass

        while not stopping.is_set():
            with lock:
                now = time.monotonic()
                busy = view['busy']
                if not busy and state.mode == 'running' and state.left(now) <= 0:
                    state.stop()
                    log('timer_elapsed', state.subject)
                    speak('Time is up. What did you get done, and was there anything you found difficult?')
                new_position = sign * encoder.position
                delta, position = new_position - position, new_position
                if delta and not view['busy']:
                    if state.turn(delta):
                        view['note'] = f'{state.minutes} minutes selected. Hold the knob for 1.2 seconds to start.'
                        log('selected_minutes', state.minutes)
                    else:
                        view['note'] = 'Duration is fixed for this session. X stops it so you can select a new time.'
                event = edges.update(not button.value, now)
                if event == 'tap':
                    launch(listen, 'Listening')
                elif event == 'hold':
                    if view['busy'] or edit:
                        view['note'] = 'Wait until the device and wizard are ready, then hold again.'
                    else:
                        action = state.hold(now)
                        log('knob_hold', action)
                        if action == 'missing_subject':
                            view['note'] = 'Wizard: press S to set the subject before starting.'
                        elif action == 'start':
                            message = f'Starting a {args.test_seconds:g} second demonstration.' if args.test_seconds else f'Starting {state.minutes} minutes of {state.subject}.'
                            speak(message, lambda: (state.start(time.monotonic()), log('timer_started', {'subject': state.subject, 'minutes': state.minutes, 'actual_seconds': state.remaining})))
                        else:
                            log('timer_' + action, state.remaining)
                            speak('Timer paused.' if action == 'pause' else 'Timer resumed.')

                key = screen.getch()
                if edit:
                    if key == 27:
                        edit = None
                    elif key in (10, 13):
                        if text.strip():
                            if edit == 'subject':
                                state.subject = text.strip()
                                log('wizard_subject', state.subject)
                            else:
                                log('wizard_custom_reply', text.strip())
                                speak(text.strip())
                        edit = None
                    elif key in (8, 127, curses.KEY_BACKSPACE):
                        text = text[:-1]
                    elif 32 <= key <= 126 and len(text) < 300:
                        text += chr(key)
                elif key in (ord('q'), ord('Q')):
                    stopping.set()
                elif key in (curses.KEY_LEFT, curses.KEY_UP):
                    selected = (selected - 1) % len(labels)
                elif key in (curses.KEY_RIGHT, curses.KEY_DOWN):
                    selected = (selected + 1) % len(labels)
                elif key in (ord('x'), ord('X')) and not view['busy']:
                    state.stop()
                    log('wizard_timer_stopped', state.subject)
                    speak('Study session stopped. Turn the knob to choose a new time.')
                elif key in (ord('s'), ord('S')) and not view['busy']:
                    if state.mode != 'ready':
                        view['note'] = 'Stop the current timer with X before changing the subject.'
                    else:
                        edit, text = 'subject', ''
                elif key in (ord('u'), ord('U')) and not view['busy'] and state.mode == 'ready':
                    state.subject = view['heard']
                    log('wizard_subject', state.subject)
                elif key in (10, 13) or ord('1') <= key <= ord('7'):
                    choice = key - ord('1') if ord('1') <= key <= ord('7') else selected
                    selected = choice
                    if view['busy']:
                        view['note'] = 'Wait for recording or speaking to finish.'
                    elif choice == 6:
                        edit, text = 'reply', ''
                    elif choice == 2 and not state.subject:
                        view['note'] = 'Set the subject with S or use the transcript with U first.'
                    else:
                        replies = [
                            'What are you studying, and what would you like to get done in this session?',
                            'How much time would you like, and how will you use it? Turn the knob to set the time, then tap to tell me your plan.',
                            f'Okay, {state.subject} for {state.minutes} minutes. You can change the time, or hold the knob when you are ready to start.',
                            'Could you say that again? Tell me a little more about what you mean.',
                            'What did you get done, and what would you like to work on next?',
                            'Thanks for checking in. Your session is complete. You can come back when you are ready to study again.',
                        ]
                        if choice == 5:
                            log('wizard_session_completed', {'mode': state.mode, 'remaining_seconds': state.left(now)})
                            state.stop()
                        log('wizard_menu', labels[choice])
                        speak(replies[choice])

                phase = view['phase'] if view['busy'] else ('Paused' if state.mode == 'paused' else 'Studying' if state.mode == 'running' else 'Ready - waiting for wizard')
                colors = {'Listening': (0, 0, 100), 'Thinking': (100, 40, 0), 'Speaking': (60, 0, 80),
                          'Paused': (90, 60, 0), 'Studying': (0, 70, 0)}
                color = colors.get(phase, (0, 40, 40))
                if color != last_color:
                    pixel.fill(color)
                    last_color = color
                screen.erase()
                height, width = screen.getmaxyx()
                if height < 22 or width < 65:
                    draw(0, 'Enlarge/maximize this terminal to see the wizard menu.')
                    draw(2, f'Need 65 columns x 22 rows. Current: {width} x {height}.')
                    draw(4, 'Q quits. The menu appears as soon as the terminal is large enough.')
                    screen.refresh()
                    time.sleep(0.03)
                    continue
                left = math.ceil(state.left(now))
                draw(0, 'STUDY ASSISTANT - TERMINAL WIZARD')
                draw(2, f'Status: {phase}')
                draw(3, f'Subject: {state.subject or "(S to set)"} | Selected: {state.minutes} minutes')
                draw(4, f'Timer: {left // 60:02}:{left % 60:02} | {state.mode.upper()}')
                if args.test_seconds:
                    draw(5, f'DEMO: timer lasts {args.test_seconds:g} seconds, regardless of selected minutes')
                draw(6, 'KNOB: tap = speak | turn = minutes | hold 1.2s = start / pause / resume')
                draw(8, 'Heard: ' + view['heard'])
                draw(9, 'Device said: ' + view['reply'])
                draw(11, 'Pick reply: Left/Right + Enter, or press its number')
                draw(12, ' | '.join(f'{i+1} {name}' for i, name in enumerate(labels[:4])))
                draw(13, ' | '.join(f'{i+5} {name}' for i, name in enumerate(labels[4:])))
                draw(14, f'Selected: {selected+1} {labels[selected]}', True)
                draw(16, 'S = set subject | U = use transcript | X = stop timer | Q = quit')
                draw(18, f'{"Subject" if edit == "subject" else "Custom reply"}: {text}_  [Enter saves; Esc cancels]' if edit else view['note'])
                draw(20, 'Log: ' + log_file.name)
                screen.refresh()
            time.sleep(0.03)

    try:
        curses.wrapper(ui)
    finally:
        stopping.set()
        sd.stop()
        if worker:
            worker.join(timeout=1)
        pixel.fill((0, 0, 0))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped.')
