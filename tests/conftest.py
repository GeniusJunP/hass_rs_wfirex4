"""Load protocol.py without importing the integration, which needs Home Assistant."""

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).parent.parent / "custom_components/rs_wfirex4/protocol.py"
_SPEC = importlib.util.spec_from_file_location("rs_wfirex4_protocol", _PATH)
protocol = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = protocol
_SPEC.loader.exec_module(protocol)
