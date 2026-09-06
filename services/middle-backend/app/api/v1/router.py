from fastapi import APIRouter

from app.api.v1 import (
    auth,
    branches,
    financial_products,
    location_recommendations,
    notifications,
    reference,
    reports,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(reference.router)
api_router.include_router(branches.router)
api_router.include_router(reports.router)
api_router.include_router(notifications.router)
api_router.include_router(financial_products.router)
api_router.include_router(location_recommendations.router)
