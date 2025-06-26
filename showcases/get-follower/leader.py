from gradysim.protocol.interface import IProtocol
from gradysim.protocol.messages.telemetry import Telemetry
from gradysim.protocol.messages.mobility import GotoCoordsMobilityCommand
from gradysim.protocol.messages.communication import SendMessageCommand
from gradysim.protocol.plugin.statistics import create_statistics, finish_statistics
from message import GetPositionMessage, PositionMessage
from test_info import TEST_NAME
import time
ACCURACY = 2
ENALBE_POSITION_CHECK = 3

class LeaderProtocol(IProtocol):

    def _go_to_next_point(self):
        self.next_point = self.points[self.next_point_id]
        self.provider.send_mobility_command(GotoCoordsMobilityCommand(self.next_point[0], self.next_point[1], self.next_point[2]))
        self.next_point_id = (self.next_point_id + 1) % 4
        self.provider.schedule_timer("enable_check", self.provider.current_time() + ENALBE_POSITION_CHECK)

    def initialize(self) -> None:

        #create_statistics(self, TEST_NAME)
        self.start_time = time.time()
        self.points = [[100,100,50], [100,-100,50], [-100,-100,50], [-100,100,50]]
        self.next_point_id = self.provider.get_id()
        self.next_point = self.points[self.next_point_id]
        self.position_check_enabled = False
        self._go_to_next_point()

    def handle_timer(self, timer: str) -> None:
        if timer == "enable_check":
            self.position_check_enabled = True
    
    def handle_telemetry(self, telemetry: Telemetry) -> None:
        self.current_pos = (telemetry.current_position[0], telemetry.current_position[1], telemetry.current_position[2])
        self.provider.tracked_variables["position"] = self.current_pos
        
        if not self.position_check_enabled:
            return

        diff = abs(telemetry.current_position[0] - self.next_point[0]) + abs(telemetry.current_position[1] - self.next_point[1]) + abs(telemetry.current_position[2] - self.next_point[2])
        if diff < ACCURACY:
            self.position_check_enabled = False
            self._go_to_next_point()

    def handle_packet(self, message: str) -> None:
        message = GetPositionMessage.from_json(message)

        pos_message = PositionMessage(self.current_pos[0], self.current_pos[1], self.current_pos[2])
        self.provider.send_communication_command(SendMessageCommand(pos_message.to_json(), message.sender_id))

    def finish(self) -> None:
        #finish_statistics(self)
        print("Time elapsed: ", time.time() - self.start_time)