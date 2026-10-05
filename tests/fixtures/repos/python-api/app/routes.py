from fastapi import APIRouter

router = APIRouter()


@router.get("/events/{event_id}/tickets")
def tickets(event_id: int) -> list[dict[str, int]]:
    return [{"event_id": event_id, "remaining": 0}]
