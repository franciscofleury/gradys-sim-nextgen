import time
import unittest
from unittest.mock import patch, MagicMock

import aiohttp

from gradysim.protocol.messages.communication import CommunicationCommand, CommunicationCommandType
from gradysim.simulator.event import EventLoop
from gradysim.simulator.handler.http_communication import (
    HttpCommunicationConfiguration,
    HttpCommunicationException,
    HttpCommunicationHandler,
)
from gradysim.simulator.node import Node


class DummyEncapsulator:
    def __init__(self):
        self.received_messages = []

    def handle_packet(self, message: str):
        self.received_messages.append(message)


def _make_node(node_id, position=(0, 0, 0)):
    node = Node()
    node.id = node_id
    node.position = position
    node.protocol_encapsulator = DummyEncapsulator()
    return node


def _setup_handler(port=19000, poll_rate=0.1, node_count=3, external_networks=None):
    """Helper that creates a handler with the given number of nodes (IDs 0..n-1)."""
    config = HttpCommunicationConfiguration(
        port=port,
        poll_rate=poll_rate,
        external_networks=external_networks or []
    )
    handler = HttpCommunicationHandler(config)
    event_loop = EventLoop()
    handler.inject(event_loop)

    nodes = []
    for i in range(node_count):
        node = _make_node(i)
        handler.register_node(node)
        nodes.append(node)

    return handler, event_loop, nodes


class TestHttpCommunicationHandlerSync(unittest.TestCase):
    """Tests for HttpCommunicationHandler command handling and message delivery (no HTTP servers needed)."""

    def test_get_label(self):
        self.assertEqual(HttpCommunicationHandler.get_label(), "communication")

    def test_send_message(self):
        handler, event_loop, nodes = _setup_handler(node_count=2)

        command = CommunicationCommand(CommunicationCommandType.SEND, "hello", destination=1)
        handler.handle_command(command, nodes[0])

        # Execute the enqueue event
        event = event_loop.pop_event()
        event.callback()

        # Now poll to deliver
        handler._poll_messages(1)

        # Execute delivery events (skip poll re-schedule)
        while len(event_loop) > 0:
            ev = event_loop.pop_event()
            if "deliver" in ev.context:
                ev.callback()

        self.assertEqual(nodes[1].protocol_encapsulator.received_messages, ["hello"])
        self.assertEqual(nodes[0].protocol_encapsulator.received_messages, [])

    def test_broadcast_message(self):
        handler, event_loop, nodes = _setup_handler(node_count=3)

        command = CommunicationCommand(CommunicationCommandType.BROADCAST, "bcast")
        handler.handle_command(command, nodes[0])

        # Execute all enqueue events
        enqueue_events = []
        while len(event_loop) > 0:
            ev = event_loop.pop_event()
            if "enqueue" in ev.context:
                enqueue_events.append(ev)

        for ev in enqueue_events:
            ev.callback()

        # Poll both receivers
        handler._poll_messages(1)
        handler._poll_messages(2)

        # Execute delivery events
        while len(event_loop) > 0:
            ev = event_loop.pop_event()
            if "deliver" in ev.context:
                ev.callback()

        self.assertEqual(nodes[1].protocol_encapsulator.received_messages, ["bcast"])
        self.assertEqual(nodes[2].protocol_encapsulator.received_messages, ["bcast"])
        self.assertEqual(nodes[0].protocol_encapsulator.received_messages, [])

    def test_self_send_raises(self):
        handler, event_loop, nodes = _setup_handler(node_count=2)
        command = CommunicationCommand(CommunicationCommandType.SEND, "msg", destination=0)
        with self.assertRaises(HttpCommunicationException):
            handler.handle_command(command, nodes[0])

    def test_missing_destination_raises(self):
        handler, event_loop, nodes = _setup_handler(node_count=2)
        command = CommunicationCommand(CommunicationCommandType.SEND, "msg")
        with self.assertRaises(HttpCommunicationException):
            handler.handle_command(command, nodes[0])

    def test_nonexistent_destination_raises(self):
        handler, event_loop, nodes = _setup_handler(node_count=2)
        command = CommunicationCommand(CommunicationCommandType.SEND, "msg", destination=99)
        with self.assertRaises(HttpCommunicationException):
            handler.handle_command(command, nodes[0])

    def test_register_not_injected(self):
        handler = HttpCommunicationHandler()
        node = _make_node(0)
        with self.assertRaises(HttpCommunicationException):
            handler.register_node(node)

    def test_poll_reschedules(self):
        """Polling re-schedules itself at current_time + poll_rate."""
        handler, event_loop, nodes = _setup_handler(poll_rate=0.5, node_count=1)

        handler._poll_messages(0)

        # Should have one poll event re-scheduled
        self.assertGreater(len(event_loop), 0)
        ev = event_loop.pop_event()
        self.assertAlmostEqual(ev.timestamp, 0.5)
        self.assertIn("poll", ev.context)

    def test_multiple_messages_delivered(self):
        """Multiple messages queued for the same node are all delivered."""
        handler, event_loop, nodes = _setup_handler(node_count=2)

        for i in range(3):
            cmd = CommunicationCommand(CommunicationCommandType.SEND, f"msg{i}", destination=1)
            handler.handle_command(cmd, nodes[0])

        # Execute all enqueue events
        while len(event_loop) > 0:
            ev = event_loop.pop_event()
            ev.callback()

        handler._poll_messages(1)

        # Execute delivery events
        while len(event_loop) > 0:
            ev = event_loop.pop_event()
            if "deliver" in ev.context:
                ev.callback()

        self.assertCountEqual(nodes[1].protocol_encapsulator.received_messages, ["msg0", "msg1", "msg2"])

    def test_destructive_read(self):
        """After polling, the queue is cleared (destructive read)."""
        handler, event_loop, nodes = _setup_handler(node_count=2)

        cmd = CommunicationCommand(CommunicationCommandType.SEND, "msg", destination=1)
        handler.handle_command(cmd, nodes[0])

        # Enqueue
        event_loop.pop_event().callback()

        # First poll delivers
        handler._poll_messages(1)
        delivery_count = 0
        while len(event_loop) > 0:
            ev = event_loop.pop_event()
            if "deliver" in ev.context:
                ev.callback()
                delivery_count += 1

        self.assertEqual(delivery_count, 1)

        # Second poll should have nothing to deliver
        handler._poll_messages(1)
        delivery_count = 0
        while len(event_loop) > 0:
            ev = event_loop.pop_event()
            if "deliver" in ev.context:
                delivery_count += 1

        self.assertEqual(delivery_count, 0)

    def test_resolve_internal_id(self):
        """Internal IDs resolve correctly."""
        handler, event_loop, nodes = _setup_handler(
            node_count=2, external_networks=[(3, "http://remote:8000")]
        )
        handler._external_id_start = len(handler._nodes)
        handler._external_ranges = [(2, 5, "http://remote:8000")]

        result = handler._resolve_destination(0)
        self.assertEqual(result, ("internal", 0))

        result = handler._resolve_destination(1)
        self.assertEqual(result, ("internal", 1))

    def test_resolve_external_id(self):
        """External IDs resolve to correct network and local ID."""
        handler, event_loop, nodes = _setup_handler(
            node_count=2, external_networks=[(3, "http://remote:8000")]
        )
        handler._external_id_start = 2
        handler._external_ranges = [(2, 5, "http://remote:8000")]

        # global_id=2 -> remote local 0
        result = handler._resolve_destination(2)
        self.assertEqual(result, ("external", "http://remote:8000", 0))

        # global_id=4 -> remote local 2
        result = handler._resolve_destination(4)
        self.assertEqual(result, ("external", "http://remote:8000", 2))

    def test_resolve_multiple_external_networks(self):
        """Multiple external networks resolve IDs correctly."""
        handler, event_loop, nodes = _setup_handler(
            node_count=2, external_networks=[(2, "http://net1:8000"), (3, "http://net2:8001")]
        )
        handler._external_id_start = 2
        handler._external_ranges = [
            (2, 4, "http://net1:8000"),
            (4, 7, "http://net2:8001"),
        ]

        # ID 3 -> net1, local 1
        result = handler._resolve_destination(3)
        self.assertEqual(result, ("external", "http://net1:8000", 1))

        # ID 5 -> net2, local 1
        result = handler._resolve_destination(5)
        self.assertEqual(result, ("external", "http://net2:8001", 1))

    def test_resolve_unknown_id_raises(self):
        """IDs beyond internal + external range raise."""
        handler, event_loop, nodes = _setup_handler(
            node_count=2, external_networks=[(3, "http://remote:8000")]
        )
        handler._external_id_start = 2
        handler._external_ranges = [(2, 5, "http://remote:8000")]

        with self.assertRaises(HttpCommunicationException):
            handler._resolve_destination(99)

    def test_send_to_external_node(self):
        """SEND to an external node schedules an external send event."""
        handler, event_loop, nodes = _setup_handler(
            node_count=2, external_networks=[(3, "http://remote:8000")]
        )
        handler._external_id_start = 2
        handler._external_ranges = [(2, 5, "http://remote:8000")]

        command = CommunicationCommand(CommunicationCommandType.SEND, "ext_msg", destination=3)
        handler.handle_command(command, nodes[0])

        # Should have scheduled an external send event
        self.assertGreater(len(event_loop), 0)
        ev = event_loop.pop_event()
        self.assertIn("external", ev.context)

    def test_broadcast_includes_external(self):
        """BROADCAST sends to internal nodes AND external nodes."""
        handler, event_loop, nodes = _setup_handler(
            node_count=2, external_networks=[(2, "http://remote:8000")]
        )
        handler._external_id_start = 2
        handler._external_ranges = [(2, 4, "http://remote:8000")]

        command = CommunicationCommand(CommunicationCommandType.BROADCAST, "bcast")
        handler.handle_command(command, nodes[0])

        # Expect: 1 internal enqueue + 2 external sends = 3 events
        contexts = []
        while len(event_loop) > 0:
            ev = event_loop.pop_event()
            contexts.append(ev.context)

        internal_count = sum(1 for c in contexts if "enqueue" in c)
        external_count = sum(1 for c in contexts if "external" in c)

        self.assertEqual(internal_count, 1)  # node 1 (not sender node 0)
        self.assertEqual(external_count, 2)  # 2 external nodes

    @patch("gradysim.simulator.handler.http_communication.requests.post")
    def test_external_send_posts_with_remapped_id(self, mock_post):
        """External send POSTs to the correct URL with remapped local node_id."""
        handler, event_loop, nodes = _setup_handler(
            node_count=2, external_networks=[(3, "http://remote:8000")]
        )
        handler._external_id_start = 2
        handler._external_ranges = [(2, 5, "http://remote:8000")]

        command = CommunicationCommand(CommunicationCommandType.SEND, "ext_msg", destination=4)
        handler.handle_command(command, nodes[0])

        # Execute the scheduled event (fires the thread)
        ev = event_loop.pop_event()
        ev.callback()

        # Give the thread a moment to execute
        time.sleep(0.1)

        mock_post.assert_called_once_with(
            "http://remote:8000/send_message",
            json={"node_id": 2, "message": "ext_msg"},
            timeout=5
        )


class TestHttpCommunicationHandlerHTTP(unittest.IsolatedAsyncioTestCase):
    """Tests that verify the FastAPI HTTP endpoints work correctly."""

    def setUp(self):
        self.port = 19100
        self.handler, self.event_loop, self.nodes = _setup_handler(
            port=self.port, node_count=2
        )
        self.handler.initialize()
        # Give server time to start
        time.sleep(0.5)

    def tearDown(self):
        self.handler.finalize()

    async def test_post_and_get_endpoints(self):
        """POST /send_message adds to queue, GET /get_messages returns and clears."""
        async with aiohttp.ClientSession() as session:
            # Post a message to node 0
            async with session.post(
                f"http://127.0.0.1:{self.port}/send_message",
                json={"node_id": 0, "message": "via_http"}
            ) as resp:
                self.assertEqual(resp.status, 200)

            # Get messages for node 0
            async with session.get(
                f"http://127.0.0.1:{self.port}/get_messages",
                params={"node_id": 0}
            ) as resp:
                self.assertEqual(resp.status, 200)
                data = await resp.json()
                self.assertEqual(data, ["via_http"])

            # Second GET returns empty (destructive read)
            async with session.get(
                f"http://127.0.0.1:{self.port}/get_messages",
                params={"node_id": 0}
            ) as resp:
                data = await resp.json()
                self.assertEqual(data, [])

    async def test_post_invalid_node_returns_404(self):
        """POST to a non-existent node returns 404."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{self.port}/send_message",
                json={"node_id": 99, "message": "bad"}
            ) as resp:
                self.assertEqual(resp.status, 404)

    async def test_get_invalid_node_returns_404(self):
        """GET for a non-existent node returns 404."""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://127.0.0.1:{self.port}/get_messages",
                params={"node_id": 99}
            ) as resp:
                self.assertEqual(resp.status, 404)

    async def test_messages_isolated_per_node(self):
        """Messages posted to node 0 don't appear in node 1's queue."""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"http://127.0.0.1:{self.port}/send_message",
                json={"node_id": 0, "message": "for_node_0"}
            ) as resp:
                self.assertEqual(resp.status, 200)

            # Node 1 should have no messages
            async with session.get(
                f"http://127.0.0.1:{self.port}/get_messages",
                params={"node_id": 1}
            ) as resp:
                data = await resp.json()
                self.assertEqual(data, [])


if __name__ == "__main__":
    unittest.main()
