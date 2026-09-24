"""Simulated iPhones for development and tests without hardware."""

from ..models import MODELS, PhoneModel, get_model
from .phone import SimPhone
from .rig import SimRig, make_rig

__all__ = ["MODELS", "PhoneModel", "SimPhone", "SimRig", "get_model", "make_rig"]
