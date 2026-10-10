from fastapi import APIRouter

router = APIRouter()


@router.get("/123")
async def test():
    return {"hello": "world"}
