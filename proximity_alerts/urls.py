from django.urls import path

from proximity_alerts import views

app_name = "proximity_alerts"

urlpatterns = [
    path("", views.activation_home, name="activation_home"),
    path("activations/", views.activation_list, name="activation_list"),
    path("activations/<str:code>/", views.activation_detail, name="activation_detail"),
    path(
        "activations/<str:code>/aois/<int:aoi_number>/",
        views.aoi_detail,
        name="aoi_detail",
    ),
]
