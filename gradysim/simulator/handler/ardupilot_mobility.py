import requests
import uav_api

from dataclasses import dataclass
from typing import Dict, Tuple

from gradysim.protocol.messages.mobility import MobilityCommand, MobilityCommandType
from gradysim.protocol.messages.telemetry import Telemetry
from gradysim.protocol.position import Position, geo_to_cartesian
from gradysim.simulator.event import EventLoop
from gradysim.simulator.handler.interface import INodeHandler
from gradysim.simulator.log import label_node
from gradysim.simulator.node import Node
from uav_api.simulation import start_drone

class ArdupilotMobilityException(Exception):
    pass


@dataclass
class ArdupilotMobilityConfiguration:
    """
    Configuration class for the Ardupilot mobility handler
    """

    update_rate: float = 0.5
    """Interval in simulation seconds between Ardupilot mobility updates"""

    default_speed: float = 10
    """This is the default speed of a node in m/s"""

    reference_coordinates: Tuple[float, float, float] = (0, 0, 0)
    """
    These coordinates are used as a reference frame to convert geographical coordinates to cartesian coordinates. They
    will be used as the center of the scene and all geographical coordinates will be converted relative to it.
    """


class ArdupilotMobilityHandler(INodeHandler):
    """
    Introduces Ardupilot mobility into the simulatuon. Works by registering a regular event that
    updates every node's position based on it's target and speed. A node, through it's provider,
    can sent this handler communication commands to alter it's behaviour including it's speed 
    and current target. Nodes also recieve telemetry updates containing information pertaining
    a node's current Ardupilot mobility status.
    """

    @staticmethod
    def get_label() -> str:
        return "ardupilot_mobility"

    _event_loop: EventLoop

    nodes: Dict[int, Node]
    targets: Dict[int, Position]
    speeds: Dict[int, float]

    def __init__(self, configuration: ArdupilotMobilityConfiguration = ArdupilotMobilityConfiguration()):
        """
        Constructor for the Ardupilot mobility handler

        Args:
            configuration: Configuration for the Ardupilot mobility handler. If not set all default values will be used.
        """
        self._configuration = configuration
        self.nodes = {}
        self.apis = {}
        self.api_process = {}
        self._injected = False

    def inject(self, event_loop: EventLoop):
        self._injected = True
        self._event_loop = event_loop

        # event_loop.schedule_event(0,
        #                           self._go_to_starting_position,
        #                           "ArdupilotMobilityInitial")
        event_loop.schedule_event(event_loop.current_time + self._configuration.update_rate,
                                  self._handle_telemetry,
                                  "ArdupilotMobility")

    def register_node(self, node: Node):
        if not self._injected:
            self._ardupilot_error("Error registering node: cannot register nodes while Ardupilot mobility handler "
                                    "is uninitialized.")
        
        drone_info = start_drone(node.id, node.position)
        
        self.nodes[node.id] = node
        self.apis[node.id] = drone_info[0]
        self.api_process[node.id] = drone_info[1]

        node.position = (node.position[0], node.position[1], node.position[2]+10)

    def _ardupilot_error(self, message):
        uav_api.simulation.kill_drones()
        self._ardupilot_error(message)

    def _go_to_starting_position(self):
        for node_id in self.nodes.keys():
            node = self.nodes[node_id]
            api = self.apis[node_id]
            starting_position = node.position

            print(f"[NODE-{node_id}] Arming...")
            arm_result = requests.get(f"{api}/command/arm")
            if arm_result.status_code != 200:
                raise(f"[NODE-{node_id}] Failed to arm drone.")
            print("[NODE-{node_id}] Arming complete.")
            
            print(f"[NODE-{node_id}] Taking off...")
            takeoff_result = requests.get(f"{api}/command/takeoff", params={"alt": 10})
            if takeoff_result.status_code != 200:
                raise("[NODE-{node_id}] Failed to take off.")
            print(f"[NODE-{node_id}] Takeoff complete.")

            print(f"[NODE-{node_id}] Going to start position...")
            pos_data = {"x": starting_position[0], "y": starting_position[1], "z": -starting_position[2]} # in this step we buld the json data and convert z in protocol frame to z in ned frame (downwars)
            go_to_result = requests.post(f"{api}/movement/go_to_ned_wait", json=pos_data)
            if go_to_result.status_code != 200:
                raise(f"[NODE-{node_id}] Failed to go to start position.")
            print(f"[NODE-{node_id}] Go to start position complete.")

    def _handle_telemetry(self):
        for node_id in self.nodes.keys():
            node = self.nodes[node_id]
            api = self.apis[node_id]

            telemetry_result = requests.get(f"{api}/telemetry/ned")
            if telemetry_result.status_code != 200:
                self._ardupilot_error("Error getting telemetry from drone API")
            
            position = telemetry_result.json()["info"]["position"]
            node.position = (position["x"], position["y"], -position["z"])
            telemetry = Telemetry(current_position=node.position)
            node.protocol_encapsulator.handle_telemetry(telemetry)

        self._event_loop.schedule_event(self._event_loop.current_time + self._configuration.update_rate,
                                        self._handle_telemetry, "ArdupilotMobility")

    def handle_command(self, command: MobilityCommand, node: Node):
        """
        Performs a mobility command. This method is called by the node's 
        provider to transmit it's mobility command to the mobility handler.

        Args:
            command: Command being issued
            node: Node that issued the command
        """
        print("[ArdupilotMobilityHandler] Handling command: ", command.command_type, command.param_1, command.param_2, command.param_3)
        if node.id not in self.nodes:
            self._ardupilot_error("Error handling commands: Cannot handle command from unregistered node")

        if command.command_type == MobilityCommandType.GOTO_COORDS:
            self._goto((command.param_1, command.param_2, command.param_3), node)
        elif command.command_type == MobilityCommandType.GOTO_GEO_COORDS:
            relative_coords = geo_to_cartesian(self._configuration.reference_coordinates,
                                               (command.param_1, command.param_2, command.param_3))
            self._goto(relative_coords, node)
        # elif command.command_type == MobilityCommandType.SET_SPEED:
        #     self.speeds[node.id] = command.param_1

    def _get_ned_position(self, position: Position):
        ned_position = {
            "x": position[0],
            "y": position[1],
            "z": -position[2]
        }
        return ned_position

    def _goto(self, position: Position, node: Node):
        api = self.apis[node.id]

        ned_position = self._get_ned_position(position)
        goto_result = requests.post(f"{api}/movement/go_to_ned", json=ned_position)
        if goto_result.status_code != 200:
            self._ardupilot_error("Error sending goto command to drone API")

    def _stop(self, node: Node):
        del self.targets[node.id]

    def finalize(self):
        for node_id in self.nodes.keys():
            api_process = self.api_process[node_id]

            # Terminate the drone process
            api_process.terminate()