import serial
import time


def test_scale_connection(port='COM3', baudrate=9600):
    print(f"🚀 Starting scale test on {port} at {baudrate} baud...")

    try:
        ser = serial.Serial(port, baudrate, timeout=1)
        print("✅ Port opened. Sending request commands (O)...")

        while True:
            # Send the request command 'O' followed by Newline (Common for ViBRA)
            # You can also try b'P\r\n' (Print) or b'Q\r\n' if 'O' fails
            ser.write(b'O\r\n')

            # Give the scale a moment to respond
            time.sleep(0.5)

            if ser.in_waiting > 0:
                raw_data = ser.readline()
                decoded_data = raw_data.decode('utf-8', errors='ignore').strip()

                if decoded_data:
                    print(f"⚖️ Received: {decoded_data}")
                    numeric_part = ''.join(filter(lambda x: x.isdigit() or x == '.', decoded_data))
                    if numeric_part:
                        print(f"🎯 Extracted Weight: {numeric_part}g")

    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()


if __name__ == "__main__":
    test_scale_connection(port='COM3')