from celery import Celery

from .config import settings

celery_app = Celery("worker", broker=settings.CELERY_BROKER_URL, include=["app.tasks"])

celery_app.conf.update(
    broker_url=settings.CELERY_BROKER_URL,
    result_backend=settings.CELERY_RESULT_BACKEND,  # Wichtig: Result Backend hinzufügen!
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_send_task_events=True,
    task_send_sent_event=True,
    result_extended=True,  # Wichtig: Für vollständige Results
)


if __name__ == "__main__":
    celery_app.start()
