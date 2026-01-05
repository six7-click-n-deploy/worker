from .config import settings
from celery import Celery

celery_app = Celery(
    "worker",
    broker=settings.CELERY_BROKER_URL,
    include=["app.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_send_task_events=True,
    task_send_sent_event=True,
)

if __name__ == "__main__":
    celery_app.start()
