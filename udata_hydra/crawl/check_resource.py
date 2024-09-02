import asyncio
import time
from urllib.parse import urlparse

import aiohttp

from udata_hydra.analysis.resource import analyse_resource
from udata_hydra.crawl.process_check_data import process_check_data
from udata_hydra.crawl.utils import (
    convert_headers,
    fix_surrogates,
    has_nice_head,
    is_domain_backoff,
)
from udata_hydra.db.resource import Resource
from udata_hydra.logger import setup_logging
from udata_hydra.utils import queue

RESOURCE_RESPONSE_STATUSES = {
    "OK": "ok",
    "TIMEOUT": "timeout",
    "ERROR": "error",
    "BACKOFF": "backoff",
}

log = setup_logging()


async def check_resource(
    url: str,
    resource_id: str,
    session,
    sleep: float = 0,
    method: str = "head",
    worker_priority: str = "default",
) -> str:
    log.debug(f"check {url}, sleep {sleep}, method {method}")

    # Update resource status to CRAWLING_URL
    await Resource.update(resource_id, data={"status": "CRAWLING_URL"})

    if sleep:
        await asyncio.sleep(sleep)

    url_parsed = urlparse(url)
    domain = url_parsed.netloc

    if not domain:
        log.warning(f"[warning] not netloc in url, skipping {url}")
        # Process the check data. If it has changed, it will be sent to udata
        await process_check_data(
            {
                "resource_id": resource_id,
                "url": url,
                "error": "Not netloc in url",
                "timeout": False,
            }
        )
        return RESOURCE_RESPONSE_STATUSES["ERROR"]

    should_backoff, reason = await is_domain_backoff(domain)
    if should_backoff:
        log.info(f"backoff {domain} ({reason})")
        # skip this URL, it will come back in a next batch
        await Resource.update(resource_id, data={"status": "BACKOFF", "priority": False})
        return RESOURCE_RESPONSE_STATUSES["BACKOFF"]

    try:
        start = time.time()
        timeout = aiohttp.ClientTimeout(total=5)
        _method = getattr(session, method)
        async with _method(url, timeout=timeout, allow_redirects=True) as resp:
            end = time.time()
            if method != "get" and not has_nice_head(resp):
                return await check_resource(
                    url, resource_id, session, method="get", worker_priority=worker_priority
                )
            resp.raise_for_status()

            # Process the check data. If it has changed, it will be sent to udata
            check_id, is_first_check = await process_check_data(
                {
                    "resource_id": resource_id,
                    "url": url,
                    "domain": domain,
                    "status": resp.status,
                    "headers": convert_headers(resp.headers),
                    "timeout": False,
                    "response_time": end - start,
                },
            )

            # Update resource status to TO_PROCESS_RESOURCE
            await Resource.update(resource_id, data={"status": "TO_PROCESS_RESOURCE"})

            # Enqueue the resource for analysis
            queue.enqueue(analyse_resource, check_id, is_first_check, _priority=worker_priority)

            return RESOURCE_RESPONSE_STATUSES["OK"]

    except asyncio.exceptions.TimeoutError:
        # Process the check data. If it has changed, it will be sent to udata
        await process_check_data(
            {
                "resource_id": resource_id,
                "url": url,
                "domain": domain,
                "timeout": True,
            }
        )

        # Reset resource status so that it's not forbidden to be checked again
        await Resource.update(resource_id=resource_id, data={"status": None})

        return RESOURCE_RESPONSE_STATUSES["TIMEOUT"]

    # TODO: debug AssertionError, should be caught in DB now
    # File "[...]aiohttp/connector.py", line 991, in _create_direct_connection
    # assert port is not None
    # UnicodeError: encoding with 'idna' codec failed (UnicodeError: label too long)
    # eg http://%20Localisation%20des%20acc%C3%A8s%20des%20offices%20de%20tourisme
    except (
        aiohttp.client_exceptions.ClientError,
        AssertionError,
        UnicodeError,
    ) as e:
        error = getattr(e, "message", None) or str(e)
        # Process the check data. If it has changed, it will be sent to udata
        await process_check_data(
            {
                "resource_id": resource_id,
                "url": url,
                "domain": domain,
                "timeout": False,
                "error": fix_surrogates(error),
                "headers": convert_headers(getattr(e, "headers", {})),
                "status": getattr(e, "status", None),
            }
        )

        log.warning(f"Crawling error for url {url}", exc_info=e)

        # Reset resource status so that it's not forbidden to be checked again
        await Resource.update(resource_id=resource_id, data={"status": None})

        return RESOURCE_RESPONSE_STATUSES["ERROR"]
