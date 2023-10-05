import unittest

from simulator.messages.telemetry import Telemetry
from simulator.protocols.interface import IProtocol
from simulator.provider.interface import IProvider
from simulator.simulation import SimulationBuilder, SimulationConfiguration


class TestSimulationBuilder(unittest.TestCase):
    def test_node_created(self):
        node_count_tests = [0, 1, 5]

        for node_count in node_count_tests:
            with self.subTest(f"Testing for {node_count} nodes"):
                initialized = 0

                class DummyProtocol(IProtocol):
                    def handle_timer(self, timer: str):
                        pass

                    def handle_packet(self, message: str):
                        pass

                    def handle_telemetry(self, telemetry: Telemetry):
                        pass

                    def finish(self):
                        pass

                    def __init__(self):
                        pass

                    @classmethod
                    def instantiate(cls, provider: IProvider):
                        return cls()

                    def initialize(self, stage: int):
                        nonlocal initialized
                        initialized += 1

                builder = SimulationBuilder(SimulationConfiguration())
                for _ in range(node_count):
                    builder.add_node(DummyProtocol, (0, 0, 0))
                simulator = builder.build()
                simulator.start_simulation()
                self.assertEqual(node_count, node_count)