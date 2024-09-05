"""
NB: we can't use pytest-aiohttp helpers because
it will interfere with the rest of our async code
"""

import hashlib
import json
from datetime import datetime
from typing import Callable

import pytest
from aiohttp import RequestInfo
from aiohttp.client_exceptions import ClientError, ClientResponseError
from yarl import URL

from tests.conftest import DATASET_ID, RESOURCE_ID
from udata_hydra.db.resource import Resource
from udata_hydra.utils import is_valid_uri

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "query",
    [
        "url=https://example.com/resource-1",
        f"resource_id={RESOURCE_ID}",
    ],
)
async def test_api_get_latest_check(setup_catalog, client, query, fake_check, fake_resource_id):
    await fake_check(parsing_table=True)

    # Test invalid query
    stupid_query: str = "stupid=stupid"
    resp = await client.get(f"/api/checks/latest/?{stupid_query}")
    assert resp.status == 400

    # Test not existing resource url
    not_existing_url_query: str = "url=https://example.com/not-existing-resource"
    resp = await client.get(f"/api/checks/latest/?{not_existing_url_query}")
    assert resp.status == 404

    # Test not existing resource_id
    not_existing_resource_id_query: str = f"resource_id={fake_resource_id()}"
    resp = await client.get(f"/api/checks/latest/?{not_existing_resource_id_query}")
    assert resp.status == 404

    # Test existing resource
    resp = await client.get(f"/api/checks/latest/?{query}")
    assert resp.status == 200
    data: dict = await resp.json()
    assert data.pop("created_at")
    assert data.pop("id")
    url = "https://example.com/resource-1"
    assert data == {
        "response_time": 0.1,
        "deleted": False,
        "resource_id": RESOURCE_ID,
        "catalog_id": 1,
        "domain": "example.com",
        "error": None,
        "url": url,
        "headers": {"x-do": "you"},
        "timeout": False,
        "dataset_id": DATASET_ID,
        "status": 200,
        "parsing_error": None,
        "parsing_finished_at": None,
        "parsing_started_at": None,
        "parsing_table": hashlib.md5(url.encode("utf-8")).hexdigest(),
    }

    # Test deleted resource
    await Resource.update(resource_id=RESOURCE_ID, data={"deleted": True})
    resp = await client.get(f"/api/checks/latest/?{query}")
    assert resp.status == 410


@pytest.mark.parametrize(
    "query",
    [
        "url=https://example.com/resource-1",
        f"resource_id={RESOURCE_ID}",
    ],
)
async def test_api_get_all_checks(setup_catalog, client, query, fake_check):
    resp = await client.get(f"/api/checks/all/?{query}")
    assert resp.status == 404

    await fake_check(status=500, error="no-can-do")
    await fake_check()
    resp = await client.get(f"/api/checks/all/?{query}")
    assert resp.status == 200
    data: list = await resp.json()
    assert len(data) == 2
    first, second = data
    assert first["status"] == 200
    assert second["status"] == 500
    assert second["error"] == "no-can-do"


async def test_api_create_check_wrongly(
    setup_catalog,
    client,
    fake_check,
    fake_resource_id,
    api_headers,
):
    await fake_check()
    post_data = {"stupid_data": "stupid"}
    resp = await client.post("/api/checks/", headers=api_headers, json=post_data)
    assert resp.status == 400

    post_data = {"resource_id": str(fake_resource_id())}
    resp = await client.post("/api/checks/", headers=api_headers, json=post_data)
    assert resp.status == 404


@pytest.mark.parametrize(
    "resource",
    [
        # resource_id, status, timeout, exception
        (RESOURCE_ID, 201, False, None),
        (RESOURCE_ID, 500, False, None),
        (RESOURCE_ID, None, False, ClientError("client error")),
        (RESOURCE_ID, None, False, AssertionError),
        (RESOURCE_ID, None, False, UnicodeError),
        (RESOURCE_ID, None, True, TimeoutError),
        (
            RESOURCE_ID,
            429,
            False,
            ClientResponseError(
                RequestInfo(url="", method="", headers={}, real_url=""),
                history=(),
                message="client error",
                status=429,
            ),
        ),
    ],
)
async def test_api_create_check(
    setup_catalog,
    client,
    rmock,
    event_loop,
    db,
    resource,
    analysis_mock,
    udata_url,
    api_headers,
    api_headers_wrong_token,
):
    resource_id, resource_status, resource_timeout, resource_exception = resource
    rurl = "https://example.com/resource-1"
    params = {
        "status": resource_status,
        "headers": {"Content-LENGTH": "10", "X-Do": "you"},
        "exception": resource_exception,
    }
    rmock.head(rurl, **params)
    # mock for head fallback
    rmock.get(rurl, **params)
    rmock.put(udata_url)

    # Test API call with no token
    resp = await client.post("/api/checks/", json={"resource_id": resource_id})
    assert resp.status == 401

    # Test API call with invalid token
    resp = await client.post(
        "/api/checks/", headers=api_headers_wrong_token, json={"resource_id": resource_id}
    )
    assert resp.status == 403

    # Test the API responses cases
    api_response = await client.post(
        "/api/checks/", headers=api_headers, json={"resource_id": resource_id}
    )
    assert api_response.status == 201
    data: dict = await api_response.json()
    assert data["resource_id"] == resource_id
    assert data["url"] == rurl
    assert data["status"] == resource_status
    assert data["timeout"] == resource_timeout

    # Test check results in DB
    res = await db.fetchrow("SELECT * FROM checks WHERE url = $1", rurl)
    assert res["url"] == rurl
    assert res["status"] == resource_status
    if not resource_exception:
        assert json.loads(res["headers"]) == {
            "x-do": "you",
            # added by aioresponses :shrug:
            "content-type": "application/json",
            "content-length": "10",
        }
    assert res["timeout"] == resource_timeout
    if isinstance(resource_exception, ClientError):
        assert res["error"] == "client error"
    elif resource_status == 500:
        assert res["error"] == "Internal Server Error"
    else:
        assert not res["error"]

    # Test webhook results from mock
    webhook = rmock.requests[("PUT", URL(udata_url))][0].kwargs["json"]
    assert webhook.get("check:date")
    datetime.fromisoformat(webhook["check:date"])
    if resource_exception or resource_status == 500:
        if resource_status == 429:
            # In the case of a 429 status code, the error is on the crawler side and we can't give an availability status.
            # We expect check:available to be None.
            assert webhook.get("check:available") is None
        else:
            assert webhook.get("check:available") is False
    else:
        assert webhook.get("check:available")
        assert webhook.get("check:headers:content-type") == "application/json"
        assert webhook.get("check:headers:content-length") == 10
    if resource_timeout:
        assert webhook.get("check:timeout")
    else:
        assert webhook.get("check:timeout") is False


async def test_api_get_resource(setup_catalog, client):
    resp = await client.get(f"/api/resources/{RESOURCE_ID}")
    assert resp.status == 200
    data: dict = await resp.json()
    assert data["dataset_id"] == DATASET_ID
    assert data["resource_id"] == RESOURCE_ID
    assert data["status"] is None


@pytest.mark.parametrize("resource_status,resource_status_verbose", list(Resource.STATUSES.items()))
async def test_api_get_resource_status(
    db, client, insert_fake_resource, resource_status, resource_status_verbose
):
    await insert_fake_resource(db, status=resource_status)
    # await fake_check()
    resp = await client.get(f"/api/resources/{RESOURCE_ID}/status/")
    assert resp.status == 200
    data = await resp.json()
    assert data["resource_id"] == RESOURCE_ID
    assert data["status"] == resource_status
    assert data["status_verbose"] == resource_status_verbose
    assert is_valid_uri(data["latest_check_url"])
    assert data["latest_check_url"].endswith(f"/api/checks/latest?resource_id={RESOURCE_ID}")


async def test_api_create_resource(
    client, api_headers, api_headers_wrong_token, udata_resource_payload
):
    # Test API call with no token
    resp = await client.post(path="/api/resources/", headers=None, json=udata_resource_payload)
    assert resp.status == 401

    # Test API call with invalid token
    resp = await client.post(
        path="/api/resources/", headers=api_headers_wrong_token, json=udata_resource_payload
    )
    assert resp.status == 403

    # Test API call with invalid POST data
    stupid_post_data: dict = {"stupid": "stupid"}
    resp = await client.post(path="/api/resources/", headers=api_headers, json=stupid_post_data)
    assert resp.status == 400

    # Test API call success
    resp = await client.post(
        path="/api/resources/", headers=api_headers, json=udata_resource_payload
    )
    # assert resp.status == 201
    data: dict = await resp.json()
    assert data["id"] == "f8fb4c7b-3fc6-4448-b34f-81a9991f18ec"

    # Test API call with missing document body
    udata_resource_payload["document"] = None
    resp = await client.post(
        path="/api/resources/", headers=api_headers, json=udata_resource_payload
    )
    assert resp.status == 400
    text = await resp.text()
    assert text == "Missing document body"


async def test_api_update_resource(client, api_headers, api_headers_wrong_token):
    # Test invalid PUT data
    stupid_post_data: dict = {"stupid": "stupid"}
    resp = await client.put(
        path=f"/api/resources/{RESOURCE_ID}/", headers=api_headers, json=stupid_post_data
    )
    assert resp.status == 400

    payload = {
        "resource_id": RESOURCE_ID,
        "dataset_id": DATASET_ID,
        "document": {
            "id": RESOURCE_ID,
            "url": "http://dev.local/",
            "title": "random title",
            "description": "random description",
            "filetype": "file",
            "type": "documentation",
            "mime": "text/plain",
            "filesize": 1024,
            "checksum_type": "sha1",
            "checksum_value": "b7b1cd8230881b18b6b487d550039949867ec7c5",
            "created_at": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
        },
    }

    # Test API call with no token
    resp = await client.put(path=f"/api/resources/{RESOURCE_ID}", headers=None, json=payload)
    assert resp.status == 401

    # Test API call with invalid token
    resp = await client.put(
        path=f"/api/resources/{RESOURCE_ID}", headers=api_headers_wrong_token, json=payload
    )
    assert resp.status == 403

    # Test API call success
    resp = await client.put(path=f"/api/resources/{RESOURCE_ID}", headers=api_headers, json=payload)
    assert resp.status == 200
    data: dict = await resp.json()
    assert data["id"] == RESOURCE_ID

    # Test API call with missing document body
    payload["document"] = None
    resp = await client.put(path=f"/api/resources/{RESOURCE_ID}", headers=api_headers, json=payload)
    assert resp.status == 400
    text: str = await resp.text()
    assert text == "Missing document body"


async def test_api_update_resource_url_since_load_catalog(setup_catalog, db, client, api_headers):
    # We modify the url for this resource
    await db.execute(
        "UPDATE catalog SET url = 'https://example.com/resource-0' "
        "WHERE resource_id = 'c4e3a9fb-4415-488e-ba57-d05269b27adf'"
    )

    # We're sending an update signal on the (dataset_id,resource_id) with the previous url.
    payload = {
        "resource_id": RESOURCE_ID,
        "dataset_id": DATASET_ID,
        "document": {
            "id": RESOURCE_ID,
            "url": "https://example.com/resource-1",
            "title": "random title",
            "description": "random description",
            "filetype": "file",
            "type": "documentation",
            "mime": "text/plain",
            "filesize": 1024,
            "checksum_type": "sha1",
            "checksum_value": "b7b1cd8230881b18b6b487d550039949867ec7c5",
            "created_at": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
        },
    }
    # It does not create any duplicated resource.
    # The existing entry get updated accordingly.

    # Test API call success
    resp = await client.put(path=f"/api/resources/{RESOURCE_ID}", headers=api_headers, json=payload)
    assert resp.status == 200

    res = await db.fetch(f"SELECT * FROM catalog WHERE resource_id = '{RESOURCE_ID}'")
    assert len(res) == 1
    res[0]["url"] == "https://example.com/resource-1"


async def test_api_delete_resource(client, api_headers, api_headers_wrong_token):
    NOT_EXISTING_RESOURCE_ID = "f8fb4c7b-3fc6-4448-b34f-81a9991f18ec"
    # Test invalid resource_id
    resp = await client.delete(
        path=f"/api/resources/{NOT_EXISTING_RESOURCE_ID}",
        headers=api_headers,
    )
    assert resp.status == 404

    # Test API call with no token
    resp = await client.delete(path=f"/api/resources/{RESOURCE_ID}", headers=None)
    assert resp.status == 401

    # Test API call with invalid token
    resp = await client.delete(
        path=f"/api/resources/{RESOURCE_ID}", headers=api_headers_wrong_token
    )
    assert resp.status == 403

    # Test API call success
    resp = await client.delete(path=f"/api/resources/{RESOURCE_ID}", headers=api_headers)
    assert resp.status == 200


async def test_api_get_crawler_status(setup_catalog, client, fake_check):
    resp = await client.get("/api/status/crawler")
    assert resp.status == 200
    data: dict = await resp.json()
    assert data == {
        "total": 1,
        "pending_checks": 1,
        "fresh_checks": 0,
        "checks_percentage": 0.0,
        "fresh_checks_percentage": 0.0,
    }

    await fake_check()
    resp = await client.get("/api/status/crawler")
    assert resp.status == 200
    data: dict = await resp.json()
    assert data == {
        "total": 1,
        "pending_checks": 0,
        "fresh_checks": 1,
        "checks_percentage": 100.0,
        "fresh_checks_percentage": 100.0,
    }


async def test_api_get_stats(setup_catalog, client, fake_check):
    resp = await client.get("/api/stats")
    assert resp.status == 200
    data: dict = await resp.json()
    assert data == {
        "status": [
            {"label": "error", "count": 0, "percentage": 0},
            {"label": "timeout", "count": 0, "percentage": 0},
            {"label": "ok", "count": 0, "percentage": 0},
        ],
        "status_codes": [],
    }

    # only the last one should count
    await fake_check()
    await fake_check(timeout=True, status=None)
    await fake_check(status=500, error="error")
    resp = await client.get("/api/stats")
    assert resp.status == 200
    data: dict = await resp.json()
    assert data == {
        "status": [
            {"label": "error", "count": 1, "percentage": 100.0},
            {"label": "timeout", "count": 0, "percentage": 0},
            {"label": "ok", "count": 0, "percentage": 0},
        ],
        "status_codes": [{"code": 500, "count": 1, "percentage": 100.0}],
    }


async def test_api_get_health(client) -> None:
    resp = await client.get("/api/health")
    assert resp.status == 200
