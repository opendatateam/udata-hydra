import logging
from dotenv import load_dotenv

from udata_datalake_service.consumer import consume_kafka


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    consume_kafka()
