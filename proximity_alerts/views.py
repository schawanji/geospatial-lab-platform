import json

from django.shortcuts import render, get_object_or_404
from django.shortcuts import render
from django.urls import reverse

# Create your views here.
from proximity_alerts.models import (
    CEMSRAOI,
    CEMSRActivation,
    CEMSRProduct,
    CEMSRProductImage,
    CEMSRProductLayer,
    CEMSRProductVersion,
    CommandLog,
)

def geometry_to_geojson(geometry):
    """
    Convert a GeoDjango geometry into a GeoJSON geometry dictionary.

    Leaflet expects coordinates in EPSG:4326.
    """
    if geometry is None:
        return None

    geom = geometry.clone()

    if geom.srid and geom.srid != 4326:
        geom.transform(4326)

    return json.loads(geom.geojson)

def activation_list(request):
    activations = CEMSRActivation.objects.all().order_by("-activation_time")
    timestamp =CommandLog.objects.last()
        

    return render(
        request,
        "proximity_alerts/activation_list.html",
        {
            "activations": activations,
           'timestamp':timestamp
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

def activation_home(request):
    recent_activations = (
        CEMSRActivation.objects
        .order_by("-activation_time")[:6]
    )
    
    
    
    timestamp =CommandLog.objects.last()
        

    country_names = set()

    for countries in CEMSRActivation.objects.values_list(
        "countries",
        flat=True,
    ):
        if not isinstance(countries, list):
            continue

        for country in countries:
            if isinstance(country, dict):
                name = country.get("name")

                if name:
                    country_names.add(name)

            elif country:
                country_names.add(str(country))
    activations_geojson = {
        "type": "FeatureCollection",
        "features": [],
    }

    activations = (
        CEMSRActivation.objects
        .exclude(geom__isnull=True)
        .only(
            "code",
            "name",
            "category",
            "sub_category",
            "countries",
            "activation_time",
            "geom",
        )
    )

    for activation in activations:
        geometry = activation.geom.clone()

        if geometry.srid != 4326:
            geometry.transform(4326)

        country_values = []

        if isinstance(activation.countries, list):
            for country in activation.countries:
                if isinstance(country, dict):
                    name = country.get("name")

                    if name:
                        country_values.append(name)

                elif country:
                    country_values.append(str(country))

        activations_geojson["features"].append({
            "type": "Feature",
            "geometry": json.loads(geometry.geojson),
            "properties": {
                "code": activation.code,
                "name": activation.name,
                "category": activation.category or "",
                "sub_category": activation.sub_category or "",
                "countries": ", ".join(country_values),
                "activation_time": (
                    activation.activation_time.isoformat()
                    if activation.activation_time
                    else None
                ),
                "detail_url": reverse(
                    "proximity_alerts:activation_detail",
                    kwargs={"code": activation.code},
                ),
            },
        })           

    context = {
        'timestamp':timestamp,
        "total_activations": CEMSRActivation.objects.count(),
        "total_aois": CEMSRAOI.objects.count(),
        "total_products": CEMSRProduct.objects.count(),
        "total_countries": len(country_names),
        "recent_activations": recent_activations,
        "activations_geojson": activations_geojson,
    }

    return render(
        request,
        "proximity_alerts/home.html",
        context,
    )