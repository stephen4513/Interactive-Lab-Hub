import time
import board
import pi_servo_hat
from adafruit_seesaw import rotaryio, seesaw

SERVO_CH = 0
SERVO_MIN = 0
SERVO_MAX = 120
START_ANGLE = 60
DEGREES_PER_COUNT = 2
ENCODER_DIRECTION = -1

i2c = board.I2C()
knob = seesaw.Seesaw(i2c, addr=0x36)
encoder = rotaryio.IncrementalEncoder(knob)
last_position = ENCODER_DIRECTION * encoder.position

servo = pi_servo_hat.PiServoHat()
servo.restart()
angle = START_ANGLE
servo.move_servo_position(SERVO_CH, angle)

print("Turn the knob to move the servo. Ctrl+C stops.")

try:
    while True:
        position = ENCODER_DIRECTION * encoder.position
        change = position - last_position
        last_position = position

        if change:
            new_angle = max(
                SERVO_MIN,
                min(SERVO_MAX, angle + change * DEGREES_PER_COUNT),
            )
            if new_angle != angle:
                angle = new_angle
                servo.move_servo_position(SERVO_CH, angle)
                print(f"Servo command: {angle}")

        time.sleep(0.02)

except KeyboardInterrupt:
    print("\nStopped. Servo holds its last position.")
