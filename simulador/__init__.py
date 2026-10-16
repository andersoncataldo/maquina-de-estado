"""Núcleo de simulação de Máquinas de Estado (sem dependência de GUI)."""
# Exportações principais da infraestrutura e protocolos
from .core import Channel, ChannelConfig, EventBus, Scheduler, Simulation, make_payload
from .fsm import FSMDefinition, FSMDefinitionError, FSMEngine, Transition
from .protocols import (
    RTTEstimator,
    Segment,
    TCPConfig,
    TCPEndpoint,
    UDPEndpoint,
    internet_checksum,
    ones_complement_sum16,
    verify,
)
from .scenarios import SCENARIOS, Scenario, run_scenario

__all__ = [
    "Scheduler",
    "EventBus",
    "Channel",
    "ChannelConfig",
    "Simulation",
    "make_payload",
    "FSMDefinition",
    "FSMEngine",
    "FSMDefinitionError",
    "Transition",
    "RTTEstimator",
    "Segment",
    "UDPEndpoint",
    "TCPEndpoint",
    "TCPConfig",
    "internet_checksum",
    "ones_complement_sum16",
    "verify",
    "SCENARIOS",
    "Scenario",
    "run_scenario",
]
