"""Agent registry.

The order here is the order agents appear in the report. Keep the cheap,
context-setting agents (disk overview) first and the heavy sweeps later.
"""

from ..core import Agent
from .disk import DiskAgent
from .homebrew import HomebrewAgent
from .python_env import PythonAgent
from .node import NodeAgent
from .containers import ContainerAgent


def all_agents() -> list[Agent]:
    return [
        DiskAgent(),
        HomebrewAgent(),
        PythonAgent(),
        NodeAgent(),
        ContainerAgent(),
    ]


__all__ = ["all_agents"]
