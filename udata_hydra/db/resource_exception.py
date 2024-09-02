from asyncpg import Record

from udata_hydra import context
from udata_hydra.db.resource import Resource


class ResourceException:
    """Represents a row in the "resources_exceptions" DB table
    Resources that are too large to be processed normally but that we want to have anyway"""

    @classmethod
    async def get_all(cls) -> list[Record]:
        """
        Get all resource_exceptions from the resource_exceptions DB table
        """
        pool = await context.pool()
        async with pool.acquire() as connection:
            q = "SELECT * FROM resources_exceptions;"
            return await connection.fetch(q)

    @classmethod
    async def get_all_ids(cls) -> list[Record]:
        """
        Get all resource_ids from resource_exceptions DB table
        """
        pool = await context.pool()
        async with pool.acquire() as connection:
            q = "SELECT resource_id FROM resources_exceptions;"
            return await connection.fetch(q)

    @classmethod
    async def insert(cls, resource_id: str, table_indexes: dict[str, str]) -> Record:
        """
        Insert a new resource_exception in the resource_exceptions DB table
        """
        pool = await context.pool()

        # First, check if the resource_id exists in the catalog table
        resource: dict | None = await Resource.get(resource_id)
        if not resource:
            raise ValueError("Resource not found")

        async with pool.acquire() as connection:
            q = f"""
                INSERT INTO resources_exceptions (resource_id, table_indexes)
                VALUES ('{resource_id}', '{table_indexes}')
                RETURNING *;
            """
            return await connection.fetchrow(q)
