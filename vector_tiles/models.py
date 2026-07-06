from django.db import models
from proximity_alerts.models import CEMSRProduct,CEMSRProductLayer

class CEMSRGeoServerLayer(models.Model):
    product = models.ForeignKey(
        CEMSRProduct,
        on_delete=models.CASCADE,
        related_name="geoserver_layers",
    )

    product_layer = models.ForeignKey(
        CEMSRProductLayer,
        on_delete=models.SET_NULL,
        related_name="geoserver_layers",
        null=True,
        blank=True,
    )

    role = models.CharField(max_length=100, default="impact_layer")

    workspace = models.CharField(max_length=255, default="cemsr")
    datastore = models.CharField(max_length=255, default="cemsr_postgis")

    table_name = models.CharField(max_length=255)
    geoserver_layer_name = models.CharField(max_length=255, db_index=True)

    style_name = models.CharField(max_length=255, null=True, blank=True)

    local_zip_path = models.CharField(max_length=1000, null=True, blank=True)
    local_shapefile_path = models.CharField(max_length=1000, null=True, blank=True)
    local_sld_path = models.CharField(max_length=1000, null=True, blank=True)

    is_downloaded = models.BooleanField(default=False)
    is_imported_to_postgis = models.BooleanField(default=False)
    is_published_to_geoserver = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "geoserver_layer_name"],
                name="unique_cems_geoserver_layer_name",
            )
        ]

    def __str__(self):
        return f"{self.workspace}:{self.geoserver_layer_name}"