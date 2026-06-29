from django.urls import path

from proximity_alerts import views

app_name = "proximity_alerts"

urlpatterns = [
    path("activations/", views.activation_list, name="activation_list"),
    path("activations/<str:code>/", views.activation_detail, name="activation_detail"),
]