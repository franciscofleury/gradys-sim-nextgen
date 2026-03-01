# Introduction

![Simulator architecture](../../../assets/simulator_architecture.svg)

:::gradysim.simulator.handler

:::gradysim.simulator.handler.interface.INodeHandler

## Available Handlers

In addition to implementing your own handlers, the simulator provides several built-in handlers:

- [CommunicationHandler][gradysim.simulator.handler.communication.CommunicationHandler] - Simulates message delivery between nodes within a single simulation
- [HttpCommunicationHandler][gradysim.simulator.handler.http_communication.HttpCommunicationHandler] - Extends communication with HTTP-based cross-simulation networking, enabling multiple independent simulations to exchange messages
- [MobilityHandler][gradysim.simulator.handler.mobility.MobilityHandler] - Simulates node movement in 3D space
- [TimerHandler][gradysim.simulator.handler.timer.TimerHandler] - Schedules and fires timers
- [VisualizationHandler][gradysim.simulator.handler.visualization.VisualizationHandler] - Provides 3D visualization via WebSocket
- [AssertionHandler][gradysim.simulator.handler.assertion.AssertionHandler] - Runtime assertions for validating simulation behavior