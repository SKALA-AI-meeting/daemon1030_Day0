import asyncio
import json
import logging
import os

import aio_pika
from sqlalchemy import create_engine, text

from contracts.events.models import ReportJob
from services.report_worker.observations.report import build_report


def process(engine, job, reviewer=None):
    from services.report_worker.observations.review import enrich_report

    job = ReportJob.model_validate(job).model_dump()
    # A session advisory lock survives commits but is released on process/connection loss.
    # The model request runs outside a SQL transaction; duplicate deliveries cannot overwrite it.
    with engine.connect() as connection:
        postgres = engine.dialect.name == "postgresql"
        if postgres:
            connection.execute(
                text("SELECT pg_advisory_lock(hashtext(:id))"), {"id": job["session_id"]}
            )
            connection.commit()
        try:
            with connection.begin():
                row = (
                    connection.execute(
                        text(
                            "SELECT r.snapshot_id, r.status, s.snapshot FROM reports r JOIN sessions s ON s.id=r.session_id WHERE r.session_id=:id"
                        ),
                        {"id": job["session_id"]},
                    )
                    .mappings()
                    .first()
                )
                if not row or row["snapshot_id"] != job["snapshot_id"]:
                    raise ValueError("Invalid report snapshot reference")
                if row["status"] in ("ready", "not_configured"):
                    return row["status"]
                snapshot = row["snapshot"]
                if isinstance(snapshot, str):
                    snapshot = json.loads(snapshot)
                connection.execute(
                    text("UPDATE reports SET status='processing' WHERE session_id=:id"),
                    {"id": job["session_id"]},
                )
            status, report = build_report(snapshot)
            if status == "ready":
                report = enrich_report(snapshot, report, reviewer)
            with connection.begin():
                encoded = "CAST(:content AS JSON)" if postgres else ":content"
                connection.execute(
                    text(
                        f"UPDATE reports SET status=:status, content={encoded} WHERE session_id=:id AND snapshot_id=:snapshot"
                    ),
                    {
                        "status": status,
                        "content": json.dumps(report, ensure_ascii=False),
                        "id": job["session_id"],
                        "snapshot": job["snapshot_id"],
                    },
                )
            return status
        finally:
            if postgres:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:id))"), {"id": job["session_id"]}
                )
                connection.commit()


async def main():
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    connection = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"])
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        await channel.set_qos(prefetch_count=1)
        queue = await channel.declare_queue("dayzero.reports", durable=True)
        await channel.declare_queue("dayzero.reports.failed", durable=True)
        await channel.declare_queue(
            "dayzero.reports.retry",
            durable=True,
            arguments={
                "x-message-ttl": 5000,
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": "dayzero.reports",
            },
        )
        async with queue.iterator() as iterator:
            async for message in iterator:
                job = None
                try:
                    job = json.loads(message.body)
                    await asyncio.to_thread(process, engine, job)
                except Exception:
                    logging.exception("Report processing failed")
                    attempts = int((message.headers or {}).get("attempt", 0)) + 1
                    routing = "dayzero.reports.failed" if attempts >= 3 else "dayzero.reports.retry"
                    try:
                        with engine.begin() as db:
                            if isinstance(locals().get("job"), dict):
                                db.execute(
                                    text(
                                        "UPDATE reports SET status=:status, attempts=:attempts WHERE session_id=:id AND status NOT IN ('ready','not_configured')"
                                    ),
                                    {
                                        "status": "failed" if attempts >= 3 else "retrying",
                                        "attempts": attempts,
                                        "id": job.get("session_id", ""),
                                    },
                                )
                        await channel.default_exchange.publish(
                            aio_pika.Message(
                                body=message.body,
                                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                                headers={"attempt": attempts},
                            ),
                            routing_key=routing,
                            mandatory=True,
                        )
                    except Exception:
                        await message.reject(requeue=True)
                        continue
                await message.ack()


if __name__ == "__main__":
    asyncio.run(main())
