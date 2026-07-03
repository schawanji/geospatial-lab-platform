from django.contrib.gis.db import models


class CEMSRActivation(models.Model):
    """
    Copernicus Emergency Management Service Rapid Mapping activation.

    One activation contains multiple AOIs.
    Each AOI contains multiple products.
    Each product may contain multiple versions.
    
    CEMSRActivation
    └── CEMSRAOI
            └── CEMSRProduct
                    ├── CEMSRProductVersion
                    ├── CEMSRProductLayer
                    └── CEMSProductImage
    """

    code = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=500)
    reason = models.TextField(null=True, blank=True)

    countries = models.JSONField(default=list)  # object
    continent = models.CharField(max_length=500, null=True, blank=True)
    category = models.CharField(max_length=500, null=True, blank=True)
    sub_category = models.CharField(max_length=100, null=True, blank=True)

    activation_time = models.DateTimeField(null=True, blank=True)
    event_time = models.DateTimeField(null=True, blank=True)
    registration_time = models.DateTimeField(null=True, blank=True)
    publication_date = models.DateTimeField(null=True, blank=True)
    last_update = models.DateTimeField(null=True, blank=True)

    # Geometry values from the CEMS API are commonly supplied as WKT strings.
    centroid = models.PointField(srid=4326, null=True, blank=True)
    extent = models.MultiPolygonField(srid=4326, null=True, blank=True)
    closed = models.BooleanField(default=False)

    infobulletins = models.JSONField(default=list, blank=True)
    products_path = models.CharField(max_length=1000, null=True, blank=True)
    
    class Meta:
        ordering = ["-activation_time", "code"]
        verbose_name = "CEMS activation"
        verbose_name_plural = "CEMS activations"

    def __str__(self):
        return f"{self.code} - {self.name}"


class CEMSRAOI(models.Model):
    """Area of interest belonging to a CEMS activation."""
    activation = models.ForeignKey(
        CEMSRActivation,
        on_delete=models.CASCADE,
        related_name="aois",
    )

    aoi_number = models.PositiveIntegerField()
    aoi_name = models.CharField(max_length=255)

    blp_path = models.CharField(max_length=1000, null=True, blank=True)
    geom = models.MultiPolygonField(srid=4326, null=True, blank=True)
    
    class Meta:
        ordering = ["activation__code", "aoi_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["activation", "aoi_number"],
                name="unique_aoi_number_per_activation",
            )
        ]

    def __str__(self):
        return f"{self.activation.code} AOI {self.aoi_number}: {self.aoi_name}"


class CEMSRProduct(models.Model):
    aoi = models.ForeignKey(
         CEMSRAOI, 
          on_delete=models.CASCADE, 
          related_name="products")
    product_type = models.CharField(max_length=100, null=True, blank=True)
  

    monitoring = models.BooleanField(default=False)
    monitoring_number = models.PositiveIntegerField(null=True, blank=True)
    feasible = models.BooleanField(default=True)

    extent = models.MultiPolygonField(srid=4326, null=True, blank=True)

    expected_delivery = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ["aoi__activation__code", "aoi__aoi_number", "product_type"]

    def __str__(self):
        return f"{self.aoi.activation.code} AOI {self.aoi.aoi_number} - {self.product_type or 'Product'}"





class CEMSRProductLayer(models.Model):
     """Layer/file belonging to a product version."""
     product = models.ForeignKey(
        CEMSRProduct,
        on_delete=models.CASCADE,
        related_name="layers",
    )
     name = models.CharField(max_length=255,null=True)
     layer_type = models.CharField(max_length=100, null=True, blank=True)
     
     class Meta:
        ordering = ["product", "name"]
        
     def __str__(self):
        return self.name
   
class CEMSRProductVersion(models.Model):
    product = models.ForeignKey(
        CEMSRProduct,
        on_delete=models.CASCADE, 
        related_name="product_version"
    )
    uuid = models.UUIDField(unique=True,null=True)
    status_code = models.CharField()
    reason = models.TextField()
    number = models.PositiveIntegerField()
    delivery_time = models.DateTimeField()
    
    class Meta:
        ordering = ["product", "number"]

    def __str__(self):
        return f"{self.product} - version {self.number or 'unknown'}"


class CEMSRProductImage(models.Model):
    product = models.ForeignKey(
        CEMSRProduct,
        on_delete=models.CASCADE,
        related_name="images",
    )

    uuid = models.UUIDField(unique=True)
    is_new = models.BooleanField(default=False)
    
    sensor_type = models.CharField(max_length=100)
    resolution_class = models.CharField(max_length=100)
    sensor_name = models.CharField(max_length=255, null=True, blank=True)
    
    acquisition_time = models.DateTimeField(null=True, blank=True)
    file_name = models.CharField(max_length=500, null=True, blank=True)
    
    class Meta:
        ordering = ["-acquisition_time", "sensor_name"]

    def __str__(self):
        return self.file_name or str(self.uuid)
