"""Offline browser profile administration and controlled HTTP service.

The shared Group 2 plan stays owned by Sports Data; extend this package path in
development so local Acquisition tests resolve that single shared module.
"""
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
