from django.shortcuts import render, get_object_or_404
from django.shortcuts import render

# Create your views here.
from proximity_alerts.models import CEMSRActivation


def activation_list(request):
    activations = CEMSRActivation.objects.all().order_by("-activation_time")

    return render(
        request,
        "proximity_alerts/activation_list.html",
        {
            "activations": activations,
        },
    )


def activation_detail(request, code):
    activation = get_object_or_404(CEMSRActivation, code=code)

    return render(
        request,
        "proximity_alerts/activation_detail.html",
        {
            "activation": activation,
        },
    )