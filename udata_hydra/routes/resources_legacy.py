import json

from aiohttp import web
from asyncpg import Record
from pydantic import ValidationError

from udata_hydra.db.resource import Resource
from udata_hydra.schemas import ResourceDocumentSchema, ResourceSchema
from udata_hydra.utils import get_request_params


async def get_resource_legacy(request: web.Request) -> web.Response:
    """Endpoint to get a resource from the DB
    Respond with a 200 status code and a JSON body with the resource data
    If resource is not found, respond with a 404 status code
    """
    [resource_id] = get_request_params(request, params_names=["resource_id"])
    record: Record | None = await Resource.get(resource_id)
    if not record:
        raise web.HTTPNotFound()

    return web.Response(text=json.dumps(record, default=str), content_type="application/json")


async def create_resource_legacy(request: web.Request) -> web.Response:
    """Endpoint to receive a resource creation event from a source
    Will create a new resource in the DB "catalog" table and mark it as priority for next crawling
    Respond with a 200 status code and a JSON body with a message key set to "created"
    If error, respond with a 400 status code
    """
    try:
        payload = await request.json()
        resource = ResourceSchema.model_validate(payload)
        document = ResourceDocumentSchema.model_validate(resource.document)
    except ValidationError as err:
        raise web.HTTPBadRequest(text=err.json())

    if not document:
        raise web.HTTPBadRequest(text="Missing document body")

    await Resource.insert(
        dataset_id=resource.dataset_id,
        resource_id=str(resource.resource_id),
        url=document.url,
        priority=True,
    )

    return web.json_response({"message": "created"})


async def update_resource_legacy(request: web.Request) -> web.Response:
    """Endpoint to receive a resource update event from a source
    Will update an existing resource in the DB "catalog" table and mark it as priority for next crawling
    Respond with a 200 status code and a JSON body with a message key set to "updated"
    If error, respond with a 400 status code
    """
    try:
        payload = await request.json()
        resource = ResourceSchema.model_validate(payload)
        document = ResourceDocumentSchema.model_validate(resource.document)
    except ValidationError as err:
        raise web.HTTPBadRequest(text=err.json())

    if not document:
        raise web.HTTPBadRequest(text="Missing document body")

    await Resource.update_or_insert(resource.dataset_id, str(resource.resource_id), document.url)

    return web.json_response({"message": "updated"})


async def delete_resource_legacy(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        resource = ResourceSchema.model_validate(payload)
    except ValidationError as err:
        raise web.HTTPBadRequest(text=err.json())

    pool = request.app["pool"]
    async with pool.acquire() as connection:
        # Mark resource as deleted in catalog table
        q = f"""UPDATE catalog SET deleted = TRUE WHERE resource_id = '{str(resource.resource_id)}';"""
        await connection.execute(q)

    return web.json_response({"message": "deleted"})
