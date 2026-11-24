"""Sports Data's own Group 2 planning module.

This backend copy is independent from Acquisition's runtime planner. Extend
the package path for monorepo development tests; the backend image copies this
module into its own runtime package.
"""
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
