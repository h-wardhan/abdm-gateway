"""Job handler: retry the push of encrypted records to a HIU that could not be reached."""
from src.app.models import outbox_job as oj
from src.app.outbox.engine import handler


@handler(oj.KIND_HIU_PUSH)
def hiu_push(job):
    from src.app.services import hip_transfer_service  # late: the service imports the engine
    return hip_transfer_service.run_push_job(job)
