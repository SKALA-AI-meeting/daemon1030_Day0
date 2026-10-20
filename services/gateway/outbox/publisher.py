"""Transaction outbox -> durable RabbitMQ, with publisher confirms."""

import asyncio
import json
import logging

import aio_pika
from sqlalchemy import select

from services.gateway.persistence.database import database
from services.gateway.persistence.models import Outbox
from services.gateway.settings import Settings


async def publish_once(sessions, channel):
    await channel.declare_queue("dayzero.reports", durable=True)
    with sessions.begin() as db:
        rows = db.scalars(
            select(Outbox).where(Outbox.sent.is_(False)).with_for_update(skip_locked=True).limit(20)
        ).all()
        for row in rows:
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(row.payload).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=row.id,
                ),
                routing_key="dayzero.reports",
                mandatory=True,
            )
            row.sent = True
    return len(rows)


async def main():
    settings = Settings().validate()
    engine, sessions = database(settings.database_url)
    connection = await aio_pika.connect_robust(settings.rabbit_url)
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        while True:
            try:
                await publish_once(sessions, channel)
            except Exception:
                logging.exception("Outbox publication failed; transaction remains retryable")
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
