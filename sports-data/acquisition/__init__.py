"""Sports Data's shared Acquisition plan module.

Extend the package path only for monorepo development tests; deployed images
copy the one shared group_plan module into the component that needs it.
"""
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
