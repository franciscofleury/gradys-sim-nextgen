from gradysim.simulator.handler.communication import CommunicationHandler, CommunicationMedium
from gradysim.simulator.handler.mobility import MobilityHandler
from gradysim.simulator.handler.ardupilot_mobility import ArdupilotMobilityHandler
from gradysim.simulator.handler.timer import TimerHandler
from gradysim.simulator.handler.visualization import VisualizationHandler
from gradysim.simulator.simulation import SimulationBuilder, SimulationConfiguration, PositionScheme
from leader import LeaderProtocol
from follower import FollowerProtocol


def main():
    builder = SimulationBuilder(SimulationConfiguration(duration=60, debug=True, real_time=True))
    builder.add_handler(CommunicationHandler(CommunicationMedium(transmission_range=500)))
    builder.add_handler(TimerHandler())
    builder.add_handler(ArdupilotMobilityHandler())
    #builder.add_handler(MobilityHandler())
    #builder.add_handler(VisualizationHandler())

    builder.add_node(LeaderProtocol, (0, 0, 20))
    builder.add_node(FollowerProtocol, (0, 0, 20))

    simulation = builder.build()
    simulation.start_simulation()

if __name__ == "__main__":
    main()
