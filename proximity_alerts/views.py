from django.shortcuts import render, get_object_or_404
from django.shortcuts import render

# Create your views here.
from proximity_alerts.models import (
    CEMSRAOI,
    CEMSRActivation,
    CEMSRProduct,
    CEMSRProductImage,
    CEMSRProductLayer,
    CEMSRProductVersion,
)


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

    aois = CEMSRAOI.objects.filter(activation=activation).order_by("aoi_number")
    
    products = CEMSRProduct.objects.filter(aoi__activation__code=code).select_related(
        "aoi"
    )
    non_n_versions_count = CEMSRProductVersion.objects.filter(
        product__in=products
    ).exclude(
        status_code="N"
    ).count()
    versions = (
        CEMSRProductVersion.objects
        .filter(product__in=products)
        .select_related("product", "product__aoi")
    )
    
    layers =(
        CEMSRProductLayer.objects.filter(product__in=products).select_related("product", "product__aoi")
        )
    
    
    images =CEMSRProductImage.objects.filter(product__in=products).select_related("product", "product__aoi")
        

    context = {
        "activation": activation,
        "aois": aois,
        "products": products,
        "non_n_versions_count": non_n_versions_count,
        'layers':layers,
        "versions": versions,
        'images': images
        
    }

    return render(request, "proximity_alerts/activation_detail.html", context)


def aoi_detail(request, code, aoi_number):
    activation = get_object_or_404(CEMSRActivation, code=code)
    aoi = get_object_or_404(
        CEMSRAOI,
        activation=activation,
        aoi_number=aoi_number,
    )

    products = (
        CEMSRProduct.objects
        .filter(aoi=aoi)
        .select_related("aoi")
    )
    
    versions = (
        CEMSRProductVersion.objects
        .filter(product__in=products)
        .select_related("product", "product__aoi")
    )
    
    layers =(
        CEMSRProductLayer.objects.filter(product__in=products).select_related("product", "product__aoi")
        )
    
    
    images =CEMSRProductImage.objects.filter(product__in=products).select_related("product", "product__aoi")
        

   


    context = {
        "aoi": aoi,
        "activation": aoi.activation,
        "products": products,
        "versions": versions,
        'images':images,
        'layers':layers
    }

    return render(
        request,
        "proximity_alerts/aoi_detail.html",
        context,
    )
