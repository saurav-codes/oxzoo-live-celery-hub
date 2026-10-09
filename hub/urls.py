from django.urls import path

from river import views

urlpatterns = [
    path("", views.status_page),
    path("api/status", views.status_json),
    path("_zoo/health", views.health),
    path("_zoo/probe", views.probe),
    path("_zoo/chain/<str:chain>", views.chain),
    path("_zoo/trace/<str:trace>", views.trace),
]
