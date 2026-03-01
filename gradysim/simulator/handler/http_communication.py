import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import requests
import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gradysim.protocol.messages.communication import CommunicationCommand, CommunicationCommandType
from gradysim.simulator.event import EventLoop
from gradysim.simulator.handler.interface import INodeHandler
from gradysim.simulator.node import Node


class HttpCommunicationException(Exception):
    pass


@dataclass
class HttpCommunicationConfiguration:
    """Configuration for the HTTP communication handler."""

    port: int = 8000
    """Port for this simulation's single FastAPI server."""

    poll_rate: float = 0.1
    """Seconds between polling for incoming messages in simulation time."""

    external_networks: List[Tuple[int, str]] = field(default_factory=list)
    """List of (num_nodes, url) tuples for remote simulations."""


class MessagePayload(BaseModel):
    node_id: int
    message: str


class HttpCommunicationHandler(INodeHandler):
    """
    HTTP-based communication handler using a single FastAPI server per simulation.

    Internal messages are delivered via in-memory queues. External messages are
    pushed to remote simulations via HTTP POST. Remote simulations can POST to
    this simulation's ``/send_message`` endpoint using local node IDs (0..N-1).

    External node IDs are assigned sequentially starting from ``len(internal_nodes)``,
    following the order of ``external_networks`` in the configuration.

    Drop-in replacement for CommunicationHandler (same label: 'communication').
    """

    @staticmethod
    def get_label() -> str:
        return "communication"

    def __init__(self, configuration: HttpCommunicationConfiguration = HttpCommunicationConfiguration()):
        self._configuration = configuration
        self._nodes: Dict[int, Node] = {}
        self._message_queues: Dict[int, List[str]] = {}
        self._external_id_start: int = 0
        self._external_ranges: List[Tuple[int, int, str]] = []
        self._server: uvicorn.Server | None = None
        self._server_thread: threading.Thread | None = None
        self._injected = False
        self._logger = logging.getLogger()

    def inject(self, event_loop: EventLoop):
        self._injected = True
        self._event_loop = event_loop

    def register_node(self, node: Node):
        if not self._injected:
            raise HttpCommunicationException("Error registering node: Cannot register node on uninitialized handler")
        self._nodes[node.id] = node
        self._message_queues[node.id] = []

    def initialize(self):
        # Compute external ID ranges
        self._external_id_start = len(self._nodes)
        offset = self._external_id_start
        for num_nodes, url in self._configuration.external_networks:
            self._external_ranges.append((offset, offset + num_nodes, url))
            offset += num_nodes

        # Create and start single FastAPI server
        app = self._create_app()
        config = uvicorn.Config(app, host="0.0.0.0", port=self._configuration.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._server_thread = threading.Thread(target=self._server.run, daemon=True)
        self._server_thread.start()

        # Schedule initial poll for each node
        for node_id in self._nodes:
            def make_poll_callback(nid):
                return lambda: self._poll_messages(nid)

            self._event_loop.schedule_event(
                self._event_loop.current_time + self._configuration.poll_rate,
                make_poll_callback(node_id),
                f"HttpCommunication poll node {node_id}"
            )

    def _create_app(self) -> FastAPI:
        app = FastAPI()
        queues = self._message_queues
        nodes = self._nodes

        @app.post("/send_message")
        async def send_message(payload: MessagePayload):
            if payload.node_id not in nodes:
                return JSONResponse(status_code=404, content={"detail": f"Node {payload.node_id} not found"})
            queues[payload.node_id].append(payload.message)
            return {"status": "ok"}

        @app.get("/get_messages")
        async def get_messages(node_id: int = Query(...)):
            if node_id not in nodes:
                return JSONResponse(status_code=404, content={"detail": f"Node {node_id} not found"})
            messages = list(queues[node_id])
            queues[node_id].clear()
            return messages

        return app

    def handle_command(self, command: CommunicationCommand, sender: Node):
        if command.command_type == CommunicationCommandType.SEND:
            if command.destination is None:
                raise HttpCommunicationException(
                    "Error transmitting message: a destination is required when command type SEND is used."
                )
            if sender.id == command.destination:
                raise HttpCommunicationException(
                    "Error transmitting message: message destination is equal to sender. Try using schedule_timer."
                )

            dest_type = self._resolve_destination(command.destination)
            if dest_type[0] == "internal":
                self._enqueue_message(command.message, dest_type[1], sender.id)
            else:  # external
                self._send_external(command.message, dest_type[1], dest_type[2])

        elif command.command_type == CommunicationCommandType.BROADCAST:
            # Send to all internal nodes except sender
            for node_id in self._nodes:
                if node_id != sender.id:
                    self._enqueue_message(command.message, node_id, sender.id)

            # Send to all external nodes
            for start, end, url in self._external_ranges:
                for global_id in range(start, end):
                    remote_local_id = global_id - start
                    self._send_external(command.message, url, remote_local_id)

    def _resolve_destination(self, global_id: int):
        """Resolve a global destination ID to internal or external target.

        Returns:
            ("internal", local_id) or ("external", url, remote_local_id)

        Raises:
            HttpCommunicationException: if the ID doesn't map to any known node
        """
        if global_id in self._nodes:
            return ("internal", global_id)

        for start, end, url in self._external_ranges:
            if start <= global_id < end:
                return ("external", url, global_id - start)

        raise HttpCommunicationException(
            f"Error transmitting message: destination {global_id} does not exist."
        )

    def _enqueue_message(self, message: str, destination_id: int, sender_id: int):
        """Append a message to the destination's queue, scheduled as an event."""
        def make_enqueue_callback(msg, dest_id):
            def callback():
                self._message_queues[dest_id].append(msg)
            return callback

        self._event_loop.schedule_event(
            self._event_loop.current_time,
            make_enqueue_callback(message, destination_id),
            f"HttpCommunication enqueue {sender_id}->{destination_id}"
        )

    def _poll_messages(self, node_id: int):
        """Read and deliver all pending messages for a node, then re-schedule."""
        if node_id not in self._nodes:
            return

        node = self._nodes[node_id]

        messages = list(self._message_queues[node_id])
        self._message_queues[node_id].clear()

        for msg in messages:
            def make_deliver_callback(n, m):
                return lambda: n.protocol_encapsulator.handle_packet(m)

            self._event_loop.schedule_event(
                self._event_loop.current_time,
                make_deliver_callback(node, msg),
                f"HttpCommunication deliver to node {node_id}"
            )

        # Re-schedule poll
        def make_poll_callback(nid):
            return lambda: self._poll_messages(nid)

        self._event_loop.schedule_event(
            self._event_loop.current_time + self._configuration.poll_rate,
            make_poll_callback(node_id),
            f"HttpCommunication poll node {node_id}"
        )

    def _send_external(self, message: str, network_url: str, remote_node_id: int):
        """Fire-and-forget HTTP POST to an external simulation's /send_message endpoint."""
        def do_post(msg, url, nid):
            def callback():
                def post_thread():
                    try:
                        requests.post(
                            f"{url}/send_message",
                            json={"node_id": nid, "message": msg},
                            timeout=5
                        )
                    except Exception as e:
                        self._logger.warning(f"Failed to send external message to {url} node {nid}: {e}")
                threading.Thread(target=post_thread, daemon=True).start()
            return callback

        self._event_loop.schedule_event(
            self._event_loop.current_time,
            do_post(message, network_url, remote_node_id),
            f"HttpCommunication external send to {network_url} node {remote_node_id}"
        )

    def finalize(self):
        if self._server is not None:
            self._server.should_exit = True
        if self._server_thread is not None:
            self._server_thread.join(timeout=5)
