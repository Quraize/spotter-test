from django.urls import path

from apps.api import views

app_name = "api"

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
    path("route-plan/", views.RoutePlanView.as_view(), name="route-plan"),
]
