import json
from pathlib import Path
import pytest


@pytest.fixture
def model_data():
    return json.loads((Path(__file__).resolve().parents[1]/"examples/cantilever.json").read_text(encoding="utf-8"))

