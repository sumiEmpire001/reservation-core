import json
import tempfile
from pathlib import Path
from reservation_core import Reservations

with tempfile.TemporaryDirectory() as directory:
    service = Reservations(Path(directory) / "demo.db", clock=lambda: 0)
    service.resource("demo-room", 2)
    booking = service.hold("demo-room", "synthetic-owner", "2030-01-01T10:00:00Z", "2030-01-01T11:00:00Z", command_key="demo")
    service.change(booking["id"], "synthetic-owner", "confirm")
    print(json.dumps(service.get(booking["id"], "synthetic-owner"), indent=2))
    print("Available units:", service.availability("demo-room", "2030-01-01T10:00:00Z", "2030-01-01T11:00:00Z"))
