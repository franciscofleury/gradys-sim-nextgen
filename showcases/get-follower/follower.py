import logging

from gradysim.protocol.interface import IProtocol
from gradysim.protocol.messages.telemetry import Telemetry
from gradysim.protocol.messages.mobility import GotoCoordsMobilityCommand
from gradysim.protocol.messages.communication import SendMessageCommand
from gradysim.protocol.plugin.statistics import create_statistics, finish_statistics
from message import GetPositionMessage, PositionMessage
from test_info import TEST_NAME

GET_POSITION_INTERVAL = 2  # seconds
LEADER_ID = 0
OFFSET_X = 10
OFFSET_Y = 10

class FollowerProtocol(IProtocol):

    def initialize(self) -> None:

        create_statistics(self, TEST_NAME)

        self.provider.schedule_timer("get_position", self.provider.current_time() + GET_POSITION_INTERVAL)

    def handle_timer(self, timer: str) -> None:
        if timer == "get_position":
            get_message = GetPositionMessage(self.provider.get_id())
            self.provider.send_communication_command(SendMessageCommand(get_message.to_json(), LEADER_ID))
            self.provider.schedule_timer("get_position", self.provider.current_time() + GET_POSITION_INTERVAL)

    def handle_telemetry(self, telemetry: Telemetry) -> None:
        self.provider.tracked_variables["position"] = telemetry.current_position

    def handle_packet(self, message: str) -> None:
        message: PositionMessage = PositionMessage.from_json(message)
        self.provider.send_mobility_command(GotoCoordsMobilityCommand(message.x + OFFSET_X, message.y + OFFSET_Y, message.z))

    def finish(self) -> None:
        finish_statistics(self)