from network.brain_server import BrainServer
import time

server = BrainServer()

server.start()

print("Brain Server Running...")
print("Press CTRL+C to stop")

while True:
    time.sleep(1)