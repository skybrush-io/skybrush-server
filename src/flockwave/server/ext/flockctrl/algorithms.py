"""Functions related to the handling of algorithm codes commonly occurring
in FlockCtrl packets.
"""

from functools import singledispatch

from flockwave.protocols.flockctrl.algorithms import (
    GeigerCounterAlgorithm,
    OcularAlgorithm,
)

__all__ = ("handle_algorithm_data_packet",)


@singledispatch
def handle_algorithm_data_packet(algorithm, *, uav, data, mutate):
    """Handles a decoded data packet originating from a given UAV and a
    given algorithm.

    Parameters:
        algorithm: the algorithm that the packet originates from
        uav: the UAV that sent the packet
        data: the decoded packet; the format of this object depends
            on the algorithm class that was passed as the first argument
        mutate: the device tree mutator context for the UAV
    """
    pass


@handle_algorithm_data_packet.register
def _(algorithm: GeigerCounterAlgorithm, *, uav, data, mutate):
    with mutate() as mutator:
        uav.update_geiger_counter(
            data.position, data.iTOW, data.dose_rate, data.raw_counts, mutator
        )


@handle_algorithm_data_packet.register
def _(algorithm: OcularAlgorithm, *, uav, data, mutate):
    # TODO: so far we neglect target_position, what to do with it?
    with mutate() as mutator:
        uav.update_detected_features(data.iTOW, data.features, mutator)
