import pytest
from fastapi.testclient import TestClient
from api.index import app

@pytest.fixture
def client():
    return TestClient(app) 