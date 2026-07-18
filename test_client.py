from network.brain_client import BrainClient

client = BrainClient()

result = client.connect(
    brain_ip="127.0.0.1",
    machine_id="STAFF-PC-001",
    version="1.0.0"
)

print(result)