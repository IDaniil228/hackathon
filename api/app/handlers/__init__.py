from app.handlers.health import router as health_router
from app.handlers.test import router as test_router

routers = [
    health_router,
    test_router,
]
