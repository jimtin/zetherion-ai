"""Route registration for CGS gateway."""

from zetherion_ai.cgs_gateway.routes.internal import register_internal_routes
from zetherion_ai.cgs_gateway.routes.reporting import register_reporting_routes
from zetherion_ai.cgs_gateway.routes.runtime import register_runtime_routes

__all__ = [
    "register_runtime_routes",
    "register_internal_routes",
    "register_reporting_routes",
]
