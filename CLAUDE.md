# GrADyS-SIM NextGen

Python discrete-event simulator for prototyping and testing distributed algorithms in networked environments. Protocols are environment-agnostic: the same protocol code runs in the Python simulator, OMNeT++ (via interop), or real hardware (ArduPilot).

## Tech Stack

- **Language**: Python 3.8–3.13
- **Core dependency**: `websockets >= 12`
- **HTTP communication**: `fastapi`, `uvicorn`, `requests` (required by `HttpCommunicationHandler`)
- **Linting**: `ruff` (config: `ruff.toml`, line length 120)
- **Testing**: `pytest` + `unittest`
- **Docs**: `mkdocs` with Material theme + `mkdocstrings`
- **Build**: setuptools with setuptools-scm
- **Package**: published to PyPI as `gradysim`

## Commands

```bash
pip install -e .              # Install in dev mode
python -m pytest tests/       # Run all tests
ruff check .                  # Lint
mkdocs serve                  # Local docs preview
mkdocs build                  # Build docs
```

## Project Structure

```
gradysim/                     # Main package
├── protocol/                 # Environment-agnostic protocol logic
│   ├── interface.py          # IProtocol and IProvider base interfaces
│   ├── position.py           # Position type (Tuple[float,float,float])
│   ├── messages/             # Command/telemetry dataclasses
│   │   ├── communication.py  # CommunicationCommand, Send/Broadcast
│   │   ├── mobility.py       # MobilityCommand, GotoCoords/SetSpeed
│   │   └── telemetry.py      # Telemetry (current_position)
│   └── plugin/               # Reusable protocol plugins
│       ├── dispatcher.py     # Method interception chain (create_dispatcher)
│       ├── random_mobility.py
│       ├── mission_mobility.py
│       ├── follow_mobility.py
│       ├── statistics.py
│       └── raft/             # Raft consensus implementation
├── simulator/                # Discrete-event simulation engine
│   ├── simulation.py         # SimulationBuilder, Simulator, SimulationConfiguration
│   ├── event.py              # EventLoop (min-heap priority queue)
│   ├── node.py               # Node class (id, position, encapsulator)
│   ├── handler/              # Simulation behavior handlers
│   │   ├── interface.py      # INodeHandler base interface
│   │   ├── communication.py  # Message delivery (CommunicationMedium)
│   │   ├── mobility.py       # Movement simulation
│   │   ├── timer.py          # Timer scheduling
│   │   ├── visualization.py  # 3D visualization via WebSocket
│   │   ├── assertion.py      # Runtime assertions
│   │   ├── http_communication.py  # HTTP-based communication + cross-simulation networking
│   │   └── ardupilot_mobility.py  # Real UAV integration
│   └── extension/            # Optional extensions (camera, radio)
├── encapsulator/             # Bridges protocols to execution environments
│   ├── interface.py          # IEncapsulator base interface
│   ├── python.py             # PythonEncapsulator + PythonProvider
│   └── interop.py            # InteropEncapsulator (OMNeT++ bridge)
tests/                        # Unit tests (unittest) + integration/ (pytest)
showcases/                    # Example projects
├── ping-pong/                # Basic broadcast communication
├── simple/                   # Multi-role network (sensor/mobile/ground)
├── follow-mobility/          # Leader-follower swarm with plugins
└── raft/                     # Raft consensus protocol
docs/                         # MkDocs source files
```

## Key Concepts

- **Protocol** (`IProtocol`): User-implemented distributed algorithm. Reacts to events via 5 lifecycle methods. See `.claude/docs/protocol.md`.
- **Provider** (`IProvider`): Injected interface for protocols to communicate, move, and schedule timers.
- **Handler** (`INodeHandler`): Simulator-side component that processes protocol commands (communication, mobility, timers).
- **Encapsulator**: Adapter layer that lets the same protocol run in different execution environments.
- **Plugin**: Reusable protocol behavior attached via the dispatcher system (chain of responsibility).
- **Cross-simulation communication**: `HttpCommunicationHandler` enables multiple GrADyS simulations to exchange messages over HTTP via configurable external networks.

## Testing Conventions

- Unit tests use `unittest.TestCase` with helper factory functions for setup
- Integration tests in `tests/integration/test_samples.py` use `pytest.mark.parametrize` to run all showcases
- Test mocks follow `DummyProtocol`/`DummyEncapsulator` pattern implementing required interfaces
- Handlers are tested in isolation with their own event loop instance
- HTTP handler tests use `IsolatedAsyncioTestCase` for endpoint verification and `unittest.mock.patch` for external HTTP assertions

## Additional Documentation

Check these files when working on relevant areas:

| File | When to check |
|------|---------------|
| `.claude/docs/protocol.md` | Implementing new protocols, setting up simulations, using plugins |
| `.claude/docs/architectural_patterns.md` | Modifying simulator internals, understanding design patterns |
| `docs/` | Full user-facing documentation (MkDocs source) |
| `showcases/` | Working examples of protocols and simulation setups |
