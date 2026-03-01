# Protocol Development Guide

How to implement protocols and run simulations in GrADyS-SIM NextGen.

## Implementing a Protocol

Subclass `IProtocol` (`gradysim/protocol/interface.py:88`) and implement all 5 lifecycle methods:

| Method | When it's called | Typical use |
|--------|-----------------|-------------|
| `initialize()` | Once at simulation start (arbitrary order across nodes) | Schedule initial timers, set up plugins |
| `handle_timer(timer: str)` | When a scheduled timer fires | Periodic actions, state transitions |
| `handle_packet(message: str)` | When a message arrives from another node | Process incoming data, respond |
| `handle_telemetry(telemetry: Telemetry)` | Periodically with node mobility state | React to position changes |
| `finish()` | Once at simulation end (arbitrary order) | Cleanup, final statistics |

Protocols use `__init__()` freely for setup. The `provider` attribute is injected automatically via the `instantiate()` class method (`gradysim/protocol/interface.py:107-122`).

## Provider API

Access via `self.provider` — the protocol's interface to the environment:

- `send_communication_command(command)` — Send or broadcast messages
- `send_mobility_command(command)` — Move the node
- `schedule_timer(timer: str, timestamp: float)` — Schedule a timer at absolute simulation time
- `cancel_timer(timer: str)` — Cancel all timers with given identifier
- `current_time() -> float` — Current simulation time in seconds
- `get_id() -> int` — This node's unique ID
- `tracked_variables: Dict[str, Any]` — Dict for statistics collection

## Communication

Defined in `gradysim/protocol/messages/communication.py`. All messages are **strings** — use JSON serialization for structured data.

- **Broadcast**: `BroadcastMessageCommand(message="hello")` — sends to all nodes in range
- **Direct send**: `SendMessageCommand(message="hello", destination=node_id)` — sends to specific node
- **Generic**: `CommunicationCommand(command_type, message, destination=None)`

Communication is range-limited by `CommunicationMedium` (configured at simulation setup).

`HttpCommunicationHandler` is a drop-in replacement for `CommunicationHandler` (same label: `"communication"`), with additional HTTP-based delivery and cross-simulation capabilities.

## Mobility

Defined in `gradysim/protocol/messages/mobility.py`. Position type: `Tuple[float, float, float]` (x, y, z).

- `GotoCoordsMobilityCommand(x, y, z)` — Move to Euclidean coordinates
- `GotoGeoCoordsMobilityCommand(lat, lon, alt)` — Move to geographic coordinates
- `SetSpeedMobilityCommand(speed)` — Set speed in m/s

## Telemetry

`Telemetry` dataclass (`gradysim/protocol/messages/telemetry.py:7`) with field `current_position: Position`.

## Available Plugins

Plugins are instantiated in `__init__()` with `self` (the protocol instance). They use the dispatcher system to intercept protocol methods.

| Plugin | Module | Purpose |
|--------|--------|---------|
| `RandomMobilityPlugin` | `gradysim/protocol/plugin/random_mobility.py` | Random waypoint movement |
| `MissionMobilityPlugin` | `gradysim/protocol/plugin/mission_mobility.py` | Waypoint-following missions with loop modes (RESTART/REVERSE) |
| `MobilityLeaderPlugin` / `MobilityFollowerPlugin` | `gradysim/protocol/plugin/follow_mobility.py` | Leader-follower swarm behavior |
| `create_statistics` / `finish_statistics` | `gradysim/protocol/plugin/statistics.py` | Periodic statistics collection via `tracked_variables` |
| `create_dispatcher` | `gradysim/protocol/plugin/dispatcher.py` | Low-level method interception for custom plugins |

## Setting Up a Simulation

Use `SimulationBuilder` (`gradysim/simulator/simulation.py:382`):

1. Create builder with `SimulationConfiguration`
2. Add handlers (required for the features your protocol uses)
3. Add nodes with protocol class and initial position
4. Build and run

### Required Handlers

| Handler | Import path | Enables |
|---------|-------------|---------|
| `TimerHandler` | `gradysim.simulator.handler.timer` | `schedule_timer()` / `cancel_timer()` |
| `CommunicationHandler` | `gradysim.simulator.handler.communication` | `send_communication_command()` |
| `MobilityHandler` | `gradysim.simulator.handler.mobility` | `send_mobility_command()` + telemetry |
| `VisualizationHandler` | `gradysim.simulator.handler.visualization` | 3D WebSocket visualization |
| `AssertionHandler` | `gradysim.simulator.handler.assertion` | Runtime property assertions |
| `HttpCommunicationHandler` | `gradysim.simulator.handler.http_communication` | `send_communication_command()` via HTTP + cross-simulation |

### SimulationConfiguration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `duration` | `float?` | `None` | Max simulation time in seconds |
| `max_iterations` | `int?` | `None` | Max event loop iterations |
| `real_time` | `bool\|float` | `False` | Sync with wall clock (float = speed factor) |
| `debug` | `bool` | `False` | Enable debug logging |
| `execution_logging` | `bool` | `True` | Enable execution logs |
| `profile` | `bool` | `False` | Enable performance profiling |

### CommunicationMedium Options

Pass to `CommunicationHandler(medium)` to configure the network:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `transmission_range` | `float` | `60` | Max range in meters for message delivery |
| `delay` | `float` | `0` | Network delay in seconds |
| `failure_rate` | `float` | `0` | Message failure probability (0–1) |

### HttpCommunicationConfiguration Options

Pass to `HttpCommunicationHandler(configuration)` to configure HTTP-based communication:

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `port` | `int` | `8000` | Port for this simulation's FastAPI server |
| `poll_rate` | `float` | `0.1` | Seconds between polling for incoming messages (simulation time) |
| `external_networks` | `List[Tuple[int, str]]` | `[]` | List of `(num_nodes, url)` tuples for remote simulations |

### Cross-Simulation Communication

The `HttpCommunicationHandler` supports linking multiple independent GrADyS simulations over HTTP:

- **External networks**: configured via `external_networks` as `(num_nodes, url)` tuples, where `url` points to the remote simulation's HTTP server
- **ID assignment**: external node IDs start at `len(internal_nodes)`, assigned sequentially across networks in configuration order
- **ID remapping**: protocols use global IDs; the handler remaps to local IDs (0..N-1) on outbound POST requests
- **Push-only model**: this simulation POSTs to remote simulations' `/send_message` endpoint; remote simulations POST to this simulation's `/send_message`
- **Broadcast behavior**: sends to all internal nodes (except sender) plus all external nodes across all configured networks
- **Sender identity**: not tracked by the handler; the protocol is responsible for encoding sender info in the message payload
- **Internal delivery**: uses in-memory queues with no HTTP overhead for same-process messages

## Reference Showcases

| Showcase | Demonstrates |
|----------|-------------|
| `showcases/ping-pong/` | Basic broadcast, timers, random mobility |
| `showcases/simple/` | Multi-role protocols (sensor/mobile/ground), transmission range |
| `showcases/follow-mobility/` | Leader-follower plugins, mission waypoints |
| `showcases/raft/` | Complex distributed consensus algorithm |
