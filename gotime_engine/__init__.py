"""GOTime guidance engines: the protocol (where the health engine plugs in) and the sandbox stand-in.

docs/engine-interface.md describes the interface. Engines are looked up by name, so the API can rebuild
the engine that made a snapshot.
"""

from .protocol import GuidanceEngine, GuidanceResult, LocationInfo, Profile, Reason, WindowOptions
from .sandbox import SandboxEngine

ENGINES = {"sandbox": SandboxEngine}

__all__ = [
    "ENGINES",
    "GuidanceEngine",
    "GuidanceResult",
    "LocationInfo",
    "Profile",
    "Reason",
    "SandboxEngine",
    "WindowOptions",
]
