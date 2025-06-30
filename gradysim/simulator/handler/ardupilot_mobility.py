import requests
import time
import aiohttp
import asyncio
import logging

from dataclasses import dataclass
from typing import Dict, Tuple

from gradysim.protocol.messages.mobility import MobilityCommand, MobilityCommandType
from gradysim.protocol.messages.telemetry import Telemetry
from gradysim.protocol.position import Position, geo_to_cartesian
from gradysim.simulator.event import EventLoop
from gradysim.simulator.handler.interface import INodeHandler
from gradysim.simulator.log import label_node
from gradysim.simulator.node import Node

from uav_api.run_api import run_with_args

class ArdupilotMobilityException(Exception):
    pass

@dataclass
class AsyncHttpResponse:
    status_code: int
    json: dict

class Drone:

    async def get(self, url, params=None):
        async with self.session.get(self.drone_url + url, params=params) as response:
            if response.status == 200:
                response_obj = AsyncHttpResponse(response.status, await response.json())
                return response_obj
            else:
                raise Exception(f"Failed to fetch data from {url}. Status code: {response.status}")

    async def post(self, url, json=None):
        async with self.session.post(self.drone_url + url, json=json) as response:
            if response.status == 200:
                response_obj = AsyncHttpResponse(response.status, await response.json())
                return response_obj
            else:
                raise Exception(f"Failed to post data to {url}. Status code: {response.status}")

    def __init__(self, node_id, initial_position, logger):
        self._logger = logger
        self.node_id = node_id + 10
        self.api_process = None
        self.telemetry_requested = False
        self.position = initial_position
        self._request_queue = asyncio.Queue()
        self._request_consumer_task = None
        self.set_base_parameters()
    
    def request_telemetry(self):
        self.telemetry_requested = True
        self.add_request(self.update_telemetry)

    async def update_telemetry(self):
        telemetry_result = await self.get("/telemetry/ned")
        position = telemetry_result.json["info"]["position"]
        self.position = (position["x"], position["y"], -position["z"])
        self.telemetry_requested = False

    def move_to(self, position: Position):
        ned_position = {"x": position[0], "y": position[1], "z": -position[2]}
        self.add_request(lambda: self.post("/movement/go_to_ned", json=ned_position))

    def stop(self):
        self.add_request(lambda: self.get("/command/stop"))

    def set_speed(self, speed: int):
        self.add_request(lambda: self.get("/command/set_air_speed", params={"new_v": speed}))
    
    async def set_sim_speedup(self, speedup: int):
        await self.get("/command/set_sim_speedup", params={"sim_factor": speedup})

    def add_request(self, coro):
        self._request_queue.put_nowait(coro)

    async def _request_consumer(self):
        self._logger.debug(f"[ArdupilotMobilityHandler] Starting request consumer for node {self.node_id}")
        while True:
            request = await self._request_queue.get()
            self._logger.debug(f"[ArdupilotMobilityHandler] Processing request for node {self.node_id}")
            try:
                await request()
            except Exception as e:
                self._logger.debug(f"[ArdupilotMobilityHandler] Error handling request: {e}")
            self._request_queue.task_done()

    def set_base_parameters(self, port=None, uav_connection=None, sysid=None, speedup=None):
        if port:
            self.port = port
        else:
            self.port = 8000 + self.node_id
        if uav_connection:
            self.uav_connection = uav_connection
        else:
            self.uav_connection = f'127.0.0.1:17{171+self.node_id}'
        if sysid:
            self.sysid = sysid
        else:
            self.sysid = self.node_id
        if speedup:
            self.speedup = speedup
        else:
            self.speedup = 10

        self.drone_url = f"http://localhost:{self.port}"

    def set_session(self, session):
        self.session = session

    def start_drone(self):
        raw_args = ['--simulated', 'true', '--sysid', f'{self.sysid}', '--port', f'{self.port}', '--uav_connection', self.uav_connection, '--speedup', f'{self.speedup}', '--log_console', 'COPTER', '--log_path', '../../uav_logs']

        self.api_process = run_with_args(raw_args)

    async def goto_initial_position(self):
        self._logger.debug(f"[DRONE-{self.node_id}] API process started.")

        time.sleep(5)  # Wait for the drone API to start

        self._logger.debug(f"[DRONE-{self.node_id}] Arming...")
        arm_result = await self.get("/command/arm")
        if arm_result.status_code != 200:
            raise(f"[DRONE-{self.node_id}] Failed to arm drone.")
        self._logger.debug("[DRONE-{self.node_id}] Arming complete.")
        
        self._logger.debug(f"[DRONE-{self.node_id}] Taking off...")
        takeoff_result = await self.get("/command/takeoff", params={"alt": 10})
        if takeoff_result.status_code != 200:
            raise("[DRONE-{self.node_id}] Failed to take off.")
        self._logger.debug(f"[DRONE-{self.node_id}] Takeoff complete.")

        self._logger.debug(f"[DRONE-{self.node_id}] Going to start position...")
        pos_data = {"x": self.position[0], "y": self.position[1], "z": -self.position[2]} # in this step we buld the json data and convert z in protocol frame to z in ned frame (downwars)
        go_to_result = await self.post("/movement/go_to_ned_wait", json=pos_data)
        if go_to_result.status_code != 200:
            raise(f"[DRONE-{self.node_id}] Failed to go to start position.")
        self._logger.debug(f"[DRONE-{self.node_id}] Go to start position complete.")

        self._logger.debug(f"[DRONE-{self.node_id}] Setting simulation speedup to 1.")
        await self.set_sim_speedup(1)
        self._logger.debug(f"[DRONE-{self.node_id}] Simulation speedup set to 1.")
        self._logger.debug(f"[DRONE-{self.node_id}] Starting request consumer task.")
        self._request_consumer_task = asyncio.create_task(self._request_consumer())
        
        await self.update_telemetry()

    async def shutdown(self):
        # Cancel the request consumer task if running
        self._logger.debug(f"[DRONE-{self.node_id}] Shutting down drone API and request consumer task.")
        if self._request_consumer_task:
            self._request_consumer_task.cancel()
            try:
                await self._request_consumer_task
            except asyncio.CancelledError:
                pass

        self._logger.debug(f"[DRONE-{self.node_id}] Request consumer task cancelled.")
        # Optionally close aiohttp session if you own it
        if hasattr(self, "session") and self.session:
            await self.session.close()
        
        self._logger.debug(f"[DRONE-{self.node_id}] aiohttp session closed.")
        if self.api_process:
            self.api_process.terminate()
            self.api_process = None
        
        self._logger.debug(f"[DRONE-{self.node_id}] Drone API process terminated.")

@dataclass
class ArdupilotMobilityConfiguration:
    """
    Configuration class for the Ardupilot mobility handler
    """

    update_rate: float = 1
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
        return "mobility"

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
        self.drones = {}
        self._injected = False
        self._logger = logging.getLogger()

    def inject(self, event_loop: EventLoop):
        self._injected = True
        self._event_loop = event_loop

    def register_node(self, node: Node):
        if not self._injected:
            self._ardupilot_error("Error registering node: cannot register nodes while Ardupilot mobility handler "
                                    "is uninitialized.")
        
        self.drones[node.id] = Drone(node.id, node.position, self._logger)
        self.drones[node.id].start_drone()
        self.nodes[node.id] = node

    async def _initialize_drones(self):

        drone_tasks = []
        for node_id in self.nodes.keys():
            self.http_session = aiohttp.ClientSession()
            drone = self.drones[node_id]
            drone.set_session(self.http_session)
            drone_tasks.append(asyncio.create_task(drone.goto_initial_position()))

        await asyncio.gather(*drone_tasks)  

    async def initialize(self):
        await self._initialize_drones()     
        self._setup_telemetry()     

    def _ardupilot_error(self, message):
        for node_id in self.drones.keys():
            drone = self.drones[node_id]
            drone.end_drone()
        raise ArdupilotMobilityException(message)

    def _setup_telemetry(self):
        def send_telemetry(node_id):
            node = self.nodes[node_id]
            drone = self.drones[node_id]
            if not drone.telemetry_requested:
                node.position = drone.position
                telemetry = Telemetry(current_position=node.position)
                node.protocol_encapsulator.handle_telemetry(telemetry)
                self._logger.debug(f"Telemetry sent for node {node_id}. Value")
                drone.request_telemetry()
                self._logger.debug(f"Telemetry requested for node {node_id}.")
            else:
                self._logger.debug(f"Telemetry already requested for node {node_id}, skipping.")
            
            self._event_loop.schedule_event(self._event_loop.current_time + self._configuration.update_rate,
                                    make_send_telemetry(node_id), "ArdupilotMobility")
        
        def make_send_telemetry(node_id):
            return lambda: send_telemetry(node_id)

        for node_id in self.nodes.keys():
            self._event_loop.schedule_event(self._event_loop.current_time,
                    make_send_telemetry(node_id), "ArdupilotMobility")

    def handle_command(self, command: MobilityCommand, node: Node):
        """
        Performs a mobility command. This method is called by the node's 
        provider to transmit it's mobility command to the mobility handler.

        Args:
            command: Command being issued
            node: Node that issued the command
        """
        drone = self.drones[node.id]
        self._logger.debug(f"Handling command: {command.command_type}, {command.param_1}, {command.param_2}, {command.param_3}")
        if node.id not in self.nodes:
            self._ardupilot_error("Error handling commands: Cannot handle command from unregistered node")

        if command.command_type == MobilityCommandType.GOTO_COORDS:
            drone.move_to((command.param_1, command.param_2, command.param_3))
        elif command.command_type == MobilityCommandType.GOTO_GEO_COORDS:
            # This can be improved
            relative_coords = geo_to_cartesian(self._configuration.reference_coordinates,
                                               (command.param_1, command.param_2, command.param_3))
            drone.move_to(relative_coords, node)
        elif command.command_type == MobilityCommandType.SET_SPEED:
            drone.set_speed(command.param_1)
        elif command.command_type == MobilityCommandType.STOP:
            drone.stop()

    async def finalize(self):
        for node_id in self.drones.keys():
            drone = self.drones[node_id]
            await drone.shutdown()