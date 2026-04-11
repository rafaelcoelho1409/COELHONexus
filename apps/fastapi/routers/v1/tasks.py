"""
Task Status Router — Monitor Celery background tasks

Endpoints:
- GET  /tasks/{task_id}  — Get task status, progress, and result
- GET  /tasks            — List recent tasks
- DELETE /tasks/{task_id} — Cancel a running task
"""
from fastapi import APIRouter, HTTPException
from celery.result import AsyncResult
from celery_app import app as celery_app


router = APIRouter()


@router.get("/{task_id}")
async def get_task_status(task_id: str):
    """
    Get the status of a Celery task.

    CONCEPT: AsyncResult reads task state from Redis (the result backend).
    States: PENDING → STARTED → PROGRESS → SUCCESS or FAILURE

    PROGRESS state includes metadata from self.update_state() calls:
      {"current": 50, "total": 359, "status": "embedding"}

    SUCCESS state includes the task return value.
    FAILURE state includes the exception traceback.
    """
    result = AsyncResult(task_id, app = celery_app)

    response = {
        "task_id": task_id,
        "status": result.status,
    }

    if result.status == "PROGRESS":
        response["progress"] = result.info
    elif result.status == "SUCCESS":
        response["result"] = result.result
    elif result.status == "FAILURE":
        response["error"] = str(result.result)
    elif result.status == "STARTED":
        response["progress"] = result.info if result.info else {"status": "running"}

    return response


@router.get("")
async def list_tasks():
    """
    List recent task IDs from Celery.

    NOTE: Celery doesn't natively store a task list — it only stores
    individual task results by ID. For a full task list, use Flower's
    /api/tasks endpoint or query Redis directly.

    This returns active/reserved/scheduled tasks from live workers.
    """
    inspect = celery_app.control.inspect()

    active = inspect.active() or {}
    reserved = inspect.reserved() or {}
    scheduled = inspect.scheduled() or {}

    tasks = []

    for worker, worker_tasks in active.items():
        for t in worker_tasks:
            tasks.append({
                "task_id": t["id"],
                "name": t["name"],
                "worker": worker,
                "state": "ACTIVE",
            })

    for worker, worker_tasks in reserved.items():
        for t in worker_tasks:
            tasks.append({
                "task_id": t["id"],
                "name": t["name"],
                "worker": worker,
                "state": "RESERVED",
            })

    for worker, worker_tasks in scheduled.items():
        for t in worker_tasks:
            tasks.append({
                "task_id": t.get("request", {}).get("id", ""),
                "name": t.get("request", {}).get("name", ""),
                "worker": worker,
                "state": "SCHEDULED",
            })

    return {"tasks": tasks, "total": len(tasks)}


@router.delete("/{task_id}")
async def cancel_task(task_id: str):
    """
    Cancel a running or pending task.

    CONCEPT: revoke() tells the worker to cancel the task.
    terminate=True sends SIGTERM to the worker process running the task.
    """
    celery_app.control.revoke(task_id, terminate = True)
    return {"task_id": task_id, "status": "cancelled"}
