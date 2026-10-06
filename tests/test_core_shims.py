import sys

sys.path.insert(0, "fastapi")

from core.availability_sim import simulate_connector_availability as core_sim_fn  
from core.connectors import max_power_kw as core_max_power  
from core.scoring import haversine as core_haversine  
from utils.availability_sim import simulate_connector_availability as shim_sim_fn  
from utils.connectors import max_power_kw as shim_max_power  
from utils.scoring import haversine as shim_haversine  


def test_availability_sim_shim_reexports_the_real_function():
    assert shim_sim_fn is core_sim_fn


def test_connectors_shim_reexports_the_real_function():
    assert shim_max_power is core_max_power


def test_scoring_shim_reexports_the_real_function():
    assert shim_haversine is core_haversine
