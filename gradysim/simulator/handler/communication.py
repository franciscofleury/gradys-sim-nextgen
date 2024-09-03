import logging
import random
from dataclasses import dataclass

from gradysim.simulator.event import EventLoop
from gradysim.simulator.log import label_node
from gradysim.protocol.messages.communication import CommunicationCommand, CommunicationCommandType
from gradysim.simulator.node import Node
from gradysim.protocol.position import Position
from gradysim.simulator.handler.interface import INodeHandler

from typing import Dict, Optional

@dataclass
class NodeCommunicationConfiguration:
    transmission_range: float = 60
    """Maximum range in meters for message delivery. Messages destined to nodes outside this range will not be delivered"""

    failure_rate: float = 0
    """Failure chance between 0 and 1 for message delivery. 0 represents messages never failing and 1 always fails."""

class CommunicationDestination:
    """
    Represents the receiver for incoming communications for a specific node. Mostly
    used for logging.
    """
    node: Node
    configuration: NodeCommunicationConfiguration

    def __init__(self, node: Node, failure_rate: float, transmission_range: float):
        """
        Creates a communication destination for a specific node. Doesn't need to be
        constructed directly, is used internally in the CommunicationHandler

        Args:
            node: Node owning the destination
        """
        self.node = node
        self.configuration = NodeCommunicationConfiguration(failure_rate=failure_rate, transmission_range=transmission_range)
        self._logger = logging.getLogger()

    def receive_message(self, message: str, source: 'CommunicationSource') -> None:
        """
        Function responsible for receiving the message through the communication handler.
        """
        self._logger.debug(f"Node {self.node.id} received message from {source.node.id}")

        self.node.protocol_encapsulator.handle_packet(message)


class CommunicationSource:
    """
    Represents the outwards facing communication interface of a node. Moslty used for logging. Doesn't need to be
    constructed directly, is used internally in the CommunicationHandler
    """
    node: Node
    configurations: NodeCommunicationConfiguration

    def __init__(self, node: Node, failure_rate: float, transmission_range: float):
        """
        Creates a communication source for a specific node

        Args:
            node: Node owning the source
            configurations: Node configuration
        """
        self.node = node
        self.configurations = NodeCommunicationConfiguration(failure_rate=failure_rate, transmission_range=transmission_range)
        self._logger = logging.getLogger()

    def hand_over_message(self, message: str, endpoint: CommunicationDestination) -> None:
        """
        Function called immediately before the communication handler sends a message. Doesn't deliver the actual
        message

        Args:
            message: Message being delivered
            endpoint: Destination of the message being delivered
        """
        self._logger.debug(f"Node {self.node.id} sending message to {endpoint.node.id}")


class CommunicationException(Exception):
    pass


@dataclass
class CommunicationMedium:
    """
    Conditions through which the messages are delivered. Can influence how and when messages can be delivered.
    """
    transmission_range: float = 60
    """Maximum range in meters for message delivery. Messages destined to nodes outside this range will not be delivered"""

    delay: float = 0
    """Sets a delay in seconds for message delivery, representing network delay. Range is evaluated before the delay is applied"""

    failure_rate: float = 0
    """Failure chance between 0 and 1 for message delivery. 0 represents messages never failing and 1 always fails."""


def can_transmit(source_position: Position, destination_position: Position, communication_config: NodeCommunicationConfiguration):
    squared_distance = (destination_position[0] - source_position[0]) ** 2 + \
                       (destination_position[1] - source_position[1]) ** 2 + \
                       (destination_position[2] - source_position[2]) ** 2
    in_range = squared_distance <= (communication_config.transmission_range * communication_config.transmission_range)

    rng = True
    if communication_config.failure_rate > 0:
        rng = random.random() > communication_config.failure_rate
    return rng & in_range


class CommunicationHandler(INodeHandler):
    """
    Adds communication to the simulation. Nodes, through their providers, can
    send this handler communication commands that dictate how a message should
    be sent. This message will be delivered to the destination node.

    Messages are transmited through a [medium][gradysim.simulator.handler.communication.CommunicationMedium] that
    determines conditions like communication range and failure rate. Messages can fail to be delivered or 
    be delivered late.
    """
    @staticmethod
    def get_label() -> str:
        return "communication"

    _sources: Dict[int, CommunicationSource]
    _destinations: Dict[int, CommunicationDestination]
    _event_loop: EventLoop

    def __init__(self, communication_medium: CommunicationMedium = CommunicationMedium()):
        """
        Initializes the communication handler.

        Args:
            communication_medium: Configuration of the network conditions. If not set all default values will be used.
        """
        self._injected = False

        self._sources = {}
        self._destinations = {}
        self.communication_medium = communication_medium

        """
        Sets CommunicationHandler global instance for CommunicationController
        """
        global _active_handler
        _active_handler = self

    def inject(self, event_loop: EventLoop):
        self._injected = True
        self._event_loop = event_loop

    def register_node(self, node: Node):
        if not self._injected:
            raise CommunicationException("Error registering node: Cannot register node on uninitialized "
                                         "node handler")
        
        self._sources[node.id] = CommunicationSource(node, self.communication_medium.failure_rate, self.communication_medium.transmission_range)
        self._destinations[node.id] = CommunicationDestination(node, self.communication_medium.failure_rate, self.communication_medium.transmission_range)

    def handle_command(self, command: CommunicationCommand, sender: Node):
        """
        Performs a communication command. This method should be called by the node's
        provider to transmit a communication command to the communication handler.

        Args:
            command: Command being issued
            sender: Node issuing the command
        """
        if sender.id == command.destination:
            raise CommunicationException("Error transmitting message: message destination is equal to sender. Try "
                                         "using schedule_timer.")

        source = self._sources[sender.id]

        if command.command_type == CommunicationCommandType.BROADCAST:
            for destination, endpoint in self._destinations.items():
                if destination != sender.id:
                    self._transmit_message(command.message, source, endpoint)
        else:
            destination = command.destination
            if destination is None:
                raise CommunicationException("Error transmitting message: a destination is "
                                             "required when command type SEND is used.")
            if destination not in self._destinations:
                raise CommunicationException(f"Error transmitting message: destination {destination} does not exist.")

            self._transmit_message(command.message, source, self._destinations[destination])

    def _transmit_message(self, message: str, source: CommunicationSource, destination: CommunicationDestination):
        source.hand_over_message(message, destination)

        if can_transmit(source.node.position, destination.node.position, source.configurations):
            if self.communication_medium.delay <= 0:
                self._event_loop.schedule_event(
                    self._event_loop.current_time,
                    lambda: destination.receive_message(message, source),
                    label_node(destination.node) + " handle_packet"
                )
            else:
                self._event_loop.schedule_event(
                    self._event_loop.current_time + self.communication_medium.delay,
                    lambda: destination.receive_message(message, source),
                    label_node(destination.node) + " handle_packet"
                )

_active_handler: Optional[CommunicationHandler] = None

class CommunicationController:

    def __init__(self) -> None:
        self._communication_handler = _active_handler

        if self._communication_handler == None:
            logging.warning("No communication handler active, communication config commands will be ignored")

    def set_node_configuration(self, node_id: int, failure_rate: Optional[float] = None, transmission_range: Optional[float] = None):
        if (node_id not in self._communication_handler._sources) or (node_id not in self._communication_handler._destinations):
            logging.warning(f"Node {node_id} doesn't exist, ignoring set_node_configuration command")
            return
        if failure_rate == None:
            failure_rate = self._communication_handler.communication_medium.failure_rate
        if transmission_range == None:
            transmission_range = self._communication_handler.communication_medium.transmission_range
        node_source = self._communication_handler._sources[node_id]
        node_destination = self._communication_handler._destinations[node_id]
        if failure_rate < 0:
            logging.warning(f"Negative failure_rate, ignoring set_node_configuration command. (Node_id: {node_id}, failure_rate: {failure_rate})")
            return
        if transmission_range < 0:
            logging.warning(f"Negative transmission_range, ignoring set_node_configuration command. (Node_id: {node_id}, transmission_range: {transmission_range})")
            return
        node_source.configurations = NodeCommunicationConfiguration(failure_rate=failure_rate, transmission_range=transmission_range)
        node_destination.configurations = NodeCommunicationConfiguration(failure_rate=failure_rate, transmission_range=transmission_range)
        return
        