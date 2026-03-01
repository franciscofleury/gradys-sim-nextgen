# Architectural Patterns

Internal design patterns and conventions in GrADyS-SIM NextGen. For protocol development, see `protocol.md` instead.

## Event-Driven Discrete-Event Architecture

The simulator uses a min-heap priority queue (`gradysim/simulator/event.py:42-124`) to process events in timestamp order. Each event carries a callback, timestamp, and context string for logging/profiling.

Main loop (`gradysim/simulator/simulation.py:237-325`): pop event → execute callback → notify all handlers via `after_simulation_step()` → increment iteration.

## Provider Dependency Injection

Protocols never directly access the simulator. Instead, an `IProvider` (`gradysim/protocol/interface.py:14-85`) is injected via the `instantiate()` factory method (`gradysim/protocol/interface.py:107-122`). This decouples protocol logic from execution environment.

Two provider implementations:
- `PythonProvider` (`gradysim/encapsulator/python.py:22-127`) — delegates to simulator handlers
- `InteropProvider` (`gradysim/encapsulator/interop.py`) — collects consequences as tuples for OMNeT++

## Encapsulator Adapter Pattern

`IEncapsulator` (`gradysim/encapsulator/interface.py:10-62`) wraps protocol instances and adapts them to different execution environments:
- `PythonEncapsulator` (`gradysim/encapsulator/python.py:129-184`) — Python simulation
- `InteropEncapsulator` (`gradysim/encapsulator/interop.py`) — OMNeT++ integration

Both implement the same `encapsulate()`, `initialize()`, `handle_timer()`, `handle_packet()`, `handle_telemetry()`, `finish()` interface.

## Handler Strategy + Registry Pattern

`INodeHandler` (`gradysim/simulator/handler/interface.py:7-69`) defines the strategy interface for simulator components. Each handler:
- Self-identifies via `get_label()` (e.g., "timer", "mobility", "communication")
- Receives the event loop via `inject()`
- Hooks into node lifecycle: `register_node()`, `initialize()`, `after_simulation_step()`, `finalize()`

Handlers are registered by label in `SimulationBuilder` (`gradysim/simulator/simulation.py:416`), forming a registry keyed by string label.

Concrete handlers:
- `TimerHandler` (`gradysim/simulator/handler/timer.py:14-84`)
- `MobilityHandler` (`gradysim/simulator/handler/mobility.py:37-155`)
- `CommunicationHandler` (`gradysim/simulator/handler/communication.py:103-203`)
- `VisualizationHandler` (`gradysim/simulator/handler/visualization.py`)
- `AssertionHandler` (`gradysim/simulator/handler/assertion.py`)
- `HttpCommunicationHandler` (`gradysim/simulator/handler/http_communication.py:41-247`)

## Command Pattern

Protocol requests are encapsulated as command objects:
- `CommunicationCommand` hierarchy (`gradysim/protocol/messages/communication.py:10-46`): `SendMessageCommand`, `BroadcastMessageCommand`
- `MobilityCommand` hierarchy (`gradysim/protocol/messages/mobility.py:24-106`): `GotoCoordsMobilityCommand`, `GotoGeoCoordsMobilityCommand`, `SetSpeedMobilityCommand`

Commands use `command_type` enum discriminator with typed subclasses for parameter safety.

## Builder Pattern

`SimulationBuilder` (`gradysim/simulator/simulation.py:382-449`) provides fluent construction:
- `add_handler(handler)` — returns `self` for chaining
- `add_node(protocol, position)` — returns node index
- `build()` — creates `Simulator`, registers all nodes with all handlers

## Plugin Dispatcher (Chain of Responsibility)

`create_dispatcher()` (`gradysim/protocol/plugin/dispatcher.py:34-86`) wraps protocol methods with an interceptor chain. Handlers return `DispatchReturn.CONTINUE` or `DispatchReturn.INTERRUPT` (`gradysim/protocol/plugin/dispatcher.py:24-31`).

Used by all plugins to inject behavior into protocol lifecycle methods without modifying protocol classes:
- `RandomMobilityPlugin` registers `handle_telemetry` handler (`gradysim/protocol/plugin/random_mobility.py:103-111`)
- `MobilityFollowerPlugin` registers `handle_packet` + `handle_telemetry` handlers (`gradysim/protocol/plugin/follow_mobility.py`)
- `StatisticsPlugin` registers timer-based periodic collection (`gradysim/protocol/plugin/statistics.py`)

## Configuration Dataclass Convention

All configuration uses `@dataclass` with defaults:
- `SimulationConfiguration` (`gradysim/simulator/simulation.py:36-82`)
- `MobilityConfiguration` (`gradysim/simulator/handler/mobility.py:18-34`)
- `CommunicationMedium` (`gradysim/simulator/handler/communication.py:76-88`)
- `RandomMobilityConfig` (`gradysim/protocol/plugin/random_mobility.py:17-37`)
- `MissionMobilityConfiguration` (`gradysim/protocol/plugin/mission_mobility.py:19-36`)
- `HttpCommunicationConfiguration` (`gradysim/simulator/handler/http_communication.py:23-33`)

Pattern: dataclass with all-default fields, passed to handler/plugin constructor.

## Node Lifecycle

Three phases managed by `Simulator` (`gradysim/simulator/simulation.py:188-236`):

1. **Initialize**: Call `handler.initialize()` for all handlers, then `encapsulator.initialize()` for all nodes
2. **Execute**: Event loop pops events and executes callbacks until termination condition
3. **Finalize**: Call `encapsulator.finish()` for all nodes, then `handler.finalize()` for all handlers

Async handlers are supported — the simulator checks `asyncio.iscoroutinefunction()` and awaits if needed.

## Cross-Simulation HTTP Bridge

The `HttpCommunicationHandler` introduces an HTTP bridge pattern for inter-simulation communication:

- **Architecture**: a single FastAPI/uvicorn server per simulation, started in a daemon thread during `initialize()`
- **Endpoints**: `POST /send_message` (body: `{"node_id": int, "message": str}`) and `GET /get_messages?node_id=X` (returns and clears queued messages)
- **Thread safety**: GIL-protected list operations (`append`/`clear`) between the uvicorn thread and the simulation thread
- **External sends**: fire-and-forget `requests.post()` calls in daemon threads to avoid blocking the event loop
- **ID resolution**: `_resolve_destination()` maps a global ID to either `("internal", local_id)` or `("external", url, remote_local_id)`

## Testing Conventions

- **Unit tests**: `unittest.TestCase` in `tests/test_*.py`. Setup via helper factory functions (e.g., `setup_mobility_handler()` returns `(event_loop, handler)` tuple).
- **Integration tests**: `tests/integration/test_samples.py` uses `@pytest.mark.parametrize` to run all showcases with `_ForceFastExecution()` context manager.
- **Mocks**: `DummyProtocol` / `DummyEncapsulator` classes implement required interfaces with minimal logic. State captured via `nonlocal` variables.
- **Assertions**: Runtime property checking via decorators `@assert_always_true_for_simulation()` and `@assert_always_true_for_protocol()` (`gradysim/simulator/handler/assertion.py`).
- **HTTP handler tests** (`tests/test_http_communication_handler.py`): 17 sync `unittest.TestCase` tests for command logic, ID resolution, and external sends + 4 async `IsolatedAsyncioTestCase` tests for endpoint verification with a live server.
