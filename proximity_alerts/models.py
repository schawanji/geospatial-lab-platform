from django.db import models
from django.contrib.gis.db import models

class CEMSRActivation(models.Model):
     """
     Copernicus Emergency Management Service Rapid Mapping activation.

     This model is based on the public activations endpoint where an activation
     contains AOIs, and each AOI contains products.
     """
    
     code = models.CharField(max_length=20,unique=True,db_index=True)
     name = models.CharField(max_length=500)
     reason = models.TextField(null=True, blank=True)
     
     countries = models.JSONField(default=list) # object
     continent = models.CharField(max_length=500,null=True, blank=True)
     category   = models.CharField(max_length=500,null=True, blank=True)
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
    
    