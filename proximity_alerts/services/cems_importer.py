from __future__ import annotations

import json
import logging
from datetime import timezone as dt_timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from django.contrib.gis.geos import GEOSException, GEOSGeometry, MultiPolygon

from proximity_alerts.models import (
    CEMSRActivation,
    CEMSRAOI,
    CEMSRProduct,
    CEMSRProductImage,
    CEMSRProductLayer,
    CEMSRProductVersion,
)

logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://rapidmapping.emergency.copernicus.eu"
DETAIL_ENDPOINT = "/backend/dashboard-api/public-activations/"



class CEMSImportError(Exception):
    """Raised when the remote API payload cannot fetch or import."""
    

class CEMSActivationImporter:
    def __init__(self, *, base_url: str, timeout: int, stdout, style):
        self.base_url = base_url
        self.timeout = timeout
        self.stdout = stdout
        self.style = style

    def fetch_activation_payload(self, code: str) -> dict[str, Any]:
        url = build_detail_url(self.base_url, code)
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "django-cems-rapid-mapping-importer/1.0",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", None) or response.getcode()
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise CEMSImportError(f"API returned HTTP {exc.code} for {url}") from exc
        except URLError as exc:
            raise CEMSImportError(f"Could not reach CEMS API: {exc.reason}") from exc
        except TimeoutError as exc:
            raise CEMSImportError(f"Timed out after {self.timeout} seconds") from exc

        if status < 200 or status >= 300:
            raise CEMSImportError(f"API returned HTTP {status} for {url}")

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise CEMSImportError("CEMS API did not return valid JSON") from exc

        results = data.get("results")
        if not isinstance(results, list):
            raise CEMSImportError("CEMS API payload is missing a results array")

        if not results:
            raise CEMSImportError(f"No activation found for code {code}")

        exact_matches = [
            item
            for item in results
            if str(item.get("code", "")).upper() == code.upper()
        ]
        
        if not exact_matches:
            raise CEMSImportError(f"No exact activation match found for code {code}")

        return exact_matches[0] 

    def import_activation_payload(self, *, payload: dict[str, Any]):
        code = str(get_any(payload, "code") or "").strip().upper()

        if not code:
            raise CEMSImportError("Activation payload is missing code")
        # 1. Map and filter fields for the parent Activation
        activation_defaults = filter_model_defaults(
            CEMSRActivation,
            {
                "source_api": "rapid",
                "name": get_any(payload, "name") or code,
                "category": get_any(payload, "category") or "",
                "sub_category": get_any(payload, "subCategory", "sub_category"),
                "reason": get_any(payload, "reason"),
                "countries": normalize_countries(get_any(payload, "countries") or []),
                "continent": get_any(payload, "continent"),
                "activation_time": parse_api_datetime(
                    get_any(payload, "activationTime", "activation_time")
                ),
                "event_time": parse_api_datetime(
                    get_any(payload, "eventTime", "event_time")
                ),
                "registration_time": parse_api_datetime(
                    get_any(payload, "registrationTime", "registration_time")
                ),
                "publication_date": parse_api_datetime(
                    get_any(payload, "publicationDate", "publication_date")
                ),
                "last_update": parse_api_datetime(
                    get_any(payload, "lastUpdate", "last_update")
                ),
                "centroid": parse_point_wkt(get_any(payload, "centroid")),
                "extent": parse_polygonal_wkt(get_any(payload, "extent")),
                "infobulletins": get_any(payload, "infobulletins") or [],
                "closed": to_bool(get_any(payload, "closed"), default=False),
                "sensitive": to_bool(get_any(payload, "sensitive"), default=False),
                "products_path": get_any(payload, "productsPath", "products_path"),
                "raw_payload": payload,
            },
        )

        # 2. Save the parent Activation and capture the response as a single variable. If an activation with this code already exists, update it.
        # If it does not exist, create it.

        db_response = CEMSRActivation.objects.update_or_create(
            code=code,
            defaults=activation_defaults,
        )

        activation, _created = db_response

        # self.stdout.write( self.style.SUCCESS( f"Saved activation {activation.code}, created={_created}"))

        stats = {
            "aois": 0,
            "products": 0,
            "versions": 0,
            "images": 0,
            "layers": 0,
        }

        aois_payload = get_any(payload, "aois", "aoi", "areasOfInterest") or []

        if isinstance(aois_payload, dict):
            aois_payload = [aois_payload]

        for aoi_payload in aois_payload:
            aoi = self.import_aoi(
                activation=activation,
                payload=aoi_payload,
            )
            stats["aois"] += 1

            for product_payload in aoi_payload.get("products") or []:
                product = self.import_product(
                    activation=activation,
                    aoi=aoi,
                    payload=product_payload,
                )

                if product is None:
                    continue

                stats["products"] += 1

                version_payload = product_payload.get("version") or {}

                if self.import_product_version(
                    product=product, payload=version_payload
                ):
                    stats["versions"] += 1
                for image_payload in product_payload.get("images") or []:
                    if self.import_product_image(
                        product=product, payload=image_payload
                    ):
                        stats["images"] += 1
                for layer_payload in product_payload.get("layers") or []:
                    if self.import_product_layer(
                        product=product, payload=layer_payload
                    ):
                        stats["layers"] += 1

        return activation, stats

    def import_aoi(self, *, activation: CEMSRActivation, payload: dict[str, Any]):

        aoi_number = to_int(get_any(payload, "number", "aoiNumber", "aoi_number"))
        if aoi_number is None:
            raise CEMSImportError("AOI payload is missing a valid AOI number")

        extent = parse_polygonal_wkt(get_any(payload, "extent", "geom"))

        defaults = filter_model_defaults(
            CEMSRAOI,
            {
                "aoi_name": get_any(payload, "name", "aoiName", "aoi_name")
                or f"AOI {aoi_number}",
                "blp_path": get_any(payload, "blpPath", "blp_path"),
                "is_real_extent": to_bool(
                    get_any(payload, "isRealExtent", "is_real_extent"), default=True
                ),
                "sqkm": to_decimal(
                    get_any(payload, "sqkm", "sqKm", "areaSqKm", "area_sqkm")
                ),
                "extent": extent,
                "geom": extent,
                
            },
        )

        aoi, _created = CEMSRAOI.objects.update_or_create(
            activation=activation,
            aoi_number=aoi_number,
            defaults=defaults,
        )
        return aoi

    def import_product(
        self, *, activation: CEMSRActivation, aoi: CEMSRAOI, payload: dict[str, Any]
    ):
        product_type = str(
            get_any(payload, "type", "productType", "product_type") or ""
        ).strip()

        monitoring_number = to_int(
            get_any(payload, "monitoringNumber", "monitoring_number")
        )

        if monitoring_number is None:
            raise CEMSImportError(
                "Monitoring payload is missing a valid Monitoring number"
            )

        version = payload.get("version") or {}

        extent = parse_polygonal_wkt(get_any(payload, "extent"))

       

       

        defaults = filter_model_defaults(
            CEMSRProduct,
            {
                "source_api": "rapid",
                "activation": activation,
                "aoi": aoi,
                "product_type": product_type,
              
              
                "product_acronym": get_any(payload, "productAcronym", "product_acronym")
                or product_type,
                "monitoring": to_bool(get_any(payload, "monitoring"), default=False),
                "monitoring_number": monitoring_number,
                "feasible": to_bool(get_any(payload, "feasible"), default=True),
                "expected_delivery": parse_api_datetime(
                    get_any(payload, "expectedDelivery", "expected_delivery")
                ),
                "download_path": get_any(payload, "downloadPath", "download_path"),
                "maps_download": get_any(
                    payload,
                    "mapsDownload",
                    "maps_download",
                    "downloadPath",
                    "download_path",
                ),
                
                "analysis_scale": get_any(payload, "analysisScale", "analysis_scale"),
                "brief_description": get_any(
                    payload, "briefDescription", "brief_description"
                ),
                "determination_method": get_any(
                    payload, "determinationMethod", "determination_method"
                ),
                "drm_phase": get_any(payload, "drmPhase", "drm_phase")
                or getattr(activation, "act_drm_phase", None)
                or "Rapid Mapping",
                "status_code": get_any(version, "statusCode", "status_code"),
                "product_extent": extent,
                "stats": get_any(payload, "stats") or {},
                
            },
        )

        if not model_has_field(CEMSRProduct, "aoi"):
            raise CEMSImportError("CEMSRProduct model is missing required field: aoi")

        if model_has_field(CEMSRProduct, "aoi"):
            product, _created = CEMSRProduct.objects.update_or_create(
                
                aoi=aoi,
                product_type=product_type,
                
                monitoring_number=monitoring_number,
                defaults=defaults,
            )

            return product

    def import_product_version(
        self, *, product: CEMSRProduct, payload: dict[str, Any]
    ) -> bool:
        if CEMSRProductVersion is None or not payload:
            return False

        uuid = get_any(payload, "uuid")
        if not uuid:
            self.stdout.write(
                self.style.WARNING(f"Skipping version for {product}: missing uuid")
            )
            return False

        defaults = filter_model_defaults(
            CEMSRProductVersion,
            {
                "number": to_int(get_any(payload, "number"), default=0),
                "status_code": get_any(payload, "statusCode", "status_code") or "",
                "reason": get_any(payload, "reason"),
                "delivery_time": parse_api_datetime(
                    get_any(payload, "deliveryTime", "delivery_time")
                ),
               
            },
        )

        CEMSRProductVersion.objects.update_or_create(
            uuid=uuid,
            product=product,
            defaults=defaults,
        )
        return True

    def import_product_image(
        self, *, product: CEMSRProduct, payload: dict[str, Any]
    ) -> bool:
        if CEMSRProductImage is None:
            return False

        uuid = get_any(payload, "uuid")
        if not uuid:
            self.stdout.write(
                self.style.WARNING(f"Skipping image for {product}: missing uuid")
            )
            return False

        defaults = filter_model_defaults(
            CEMSRProductImage,
            {
                "is_new": to_bool(
                    get_any(payload, "new", "isNew", "is_new"), default=False
                ),
                "sensor_type": get_any(payload, "sensorType", "sensor_type") or "",
                "resolution_class": get_any(
                    payload, "resolutionClass", "resolution_class"
                )
                or "",
                "sensor_name": get_any(payload, "sensorName", "sensor_name"),
                "acquisition_time": parse_api_datetime(
                    get_any(payload, "acquisitionTime", "acquisition_time")
                ),
                "file_name": get_any(payload, "fileName", "file_name"),
               
            },
        )

        if model_has_field(CEMSRProductImage, "product"):
            defaults["product"] = product

        CEMSRProductImage.objects.update_or_create(
            uuid=uuid,
            defaults=defaults,
        )
        return True

    def import_product_layer(
        self, *, product: CEMSRProduct, payload: dict[str, Any]
    ) -> bool:
        if CEMSRProductLayer is None:
            return False

        name = get_any(payload, "name")
        if not name:
            self.stdout.write(
                self.style.WARNING(f"Skipping layer for {product}: missing name")
            )
            return False

        layer_type = get_any(payload, "type", "format", "layerType", "layer_type") or ""

        defaults = filter_model_defaults(
            CEMSRProductLayer,
            {
                "layer_type": layer_type,
                "raw_payload": payload,
            },
        )

        CEMSRProductLayer.objects.update_or_create(
            product=product,
            name=name,
            defaults=defaults,
        )
        return True


def normalize_base_url(base_url: str) -> str:
    base_url = (base_url or DEFAULT_BASE_URL).strip()
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    return base_url.rstrip("/")


def build_detail_url(base_url: str, code: str) -> str:
    endpoint = urljoin(base_url.rstrip("/") + "/", DETAIL_ENDPOINT.lstrip("/"))
    return f"{endpoint}?{urlencode({'code': code})}"


def get_any(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def filter_model_defaults(model_cls, defaults: dict[str, Any]) -> dict[str, Any]:
    # removes fields that do not exist on the model.
    concrete_field_names = {
        field.name
        for field in model_cls._meta.get_fields()
        if getattr(field, "concrete", False)
        and not getattr(field, "auto_created", False)
    }

    dropped = set(defaults) - concrete_field_names
    if dropped:
        logger.debug(
            "Dropped non-model fields for %s: %s",
            model_cls.__name__,
            sorted(dropped),
        )
    return {
        key: value for key, value in defaults.items() if key in concrete_field_names
    }


def normalize_countries(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_api_datetime(value: Any):
    if not value:
        return None

    if hasattr(value, "tzinfo"):
        dt = value
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = parse_datetime(raw)

    if dt is None:
        logger.warning("Could not parse datetime value %r", value)
        return None

    if timezone.is_naive(dt):
        return timezone.make_aware(dt, dt_timezone.utc)
    return dt


def parse_point_wkt(value: Any):
    geom = parse_geos_wkt(value)
    if geom is None:
        return None

    if geom.geom_type != "Point":
        logger.warning("Expected Point WKT, got %s", geom.geom_type)
        return None

    return geom


def parse_polygonal_wkt(value: Any):
    geom = parse_geos_wkt(value)
    if geom is None:
        return None

    if geom.geom_type == "MultiPolygon":
        return geom

    if geom.geom_type == "Polygon":
        multipolygon = MultiPolygon(geom, srid=geom.srid or 4326)
        return multipolygon

    logger.warning("Expected Polygon/MultiPolygon WKT, got %s", geom.geom_type)
    return None


def parse_geos_wkt(value: Any):
    if not value:
        return None

    try:
        geom = GEOSGeometry(str(value), srid=4326)
    except (GEOSException, ValueError, TypeError) as exc:
        logger.warning("Could not parse WKT %r: %s", value, exc)
        return None

    if not geom.srid:
        geom.srid = 4326

    return geom


def to_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y"}:
        return True
    if normalized in {"0", "false", "f", "no", "n"}:
        return False
    return default


def to_int(value: Any, *, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def model_has_field(model_cls, field_name: str) -> bool:
    return field_name in {field.name for field in model_cls._meta.get_fields()}
