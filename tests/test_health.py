"""Tests for the /health endpoint."""


async def test_health_returns_ok(client):
    """GET /health returns 200 with status ok and database connected."""
    response = await client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"


async def test_health_response_has_timestamp(client):
    """Health response includes a UTC timestamp string."""
    response = await client.get("/health")
    data = response.json()
    assert "timestamp" in data
    # Timestamp should be a parseable ISO 8601 string
    assert data["timestamp"] is not None
    assert len(data["timestamp"]) > 0


async def test_health_response_has_version(client):
    """Health response includes version 0.1.0."""
    response = await client.get("/health")
    data = response.json()
    assert data["version"] == "0.1.0"
