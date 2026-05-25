import json
import socket
import time


class SensorStreamPublisher:
    def __init__(self, udp_host="127.0.0.1", udp_port=8765, source="omnigibson"):
        self.addr = (udp_host, int(udp_port))
        self.source = source
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sequence = 0

    def publish(self, sim_time, readings):
        packet = {
            "source": self.source,
            "sequence": self.sequence,
            "wall_time": time.time(),
            "sim_time": float(sim_time),
            "readings": readings,
        }
        payload = json.dumps(packet, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.sock.sendto(payload, self.addr)
        self.sequence += 1
        return packet

    def close(self):
        self.sock.close()
