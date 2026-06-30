from __future__ import annotations

import json
import logging
from datetime import timezone as dt_timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from django.contrib.gis.geos import GEOSException, GEOSGeometry, MultiPolygon
from decimal import Decimal, InvalidOperation

from proximity_alerts.models import CEMSRActivation ,CEMSRAOI



logger = logging.getLogger(__name__)

        
DEFAULT_BASE_URL = "https://rapidmapping.emergency.copernicus.eu"
DETAIL_ENDPOINT = "/backend/dashboard-api/public-activations/" 

class CEMSImportError(Exception):
    """Raised when the remote API payload cannot fetch or import."""

class Command(BaseCommand):
    help = 'Fetch one Copernicus EMS Rapid Mapping activation in GeoDjango Models'
    
    
    def add_arguments(self, parser):
        parser.add_argument(
            "codes",
            nargs="+",
            help="Activation code(s), for example EMSR842 EMSR568.",
        )
        parser.add_argument(
            "--base-url",
            default=getattr(settings, "CEMS_RAPID_MAPPING_BASE_URL", DEFAULT_BASE_URL),
            help=(
                "Base URL for the Rapid Mapping API. Defaults to settings."
                "CEMS_RAPID_MAPPING_BASE_URL or the public Copernicus host."
            ),
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=45,
            help="HTTP timeout in seconds. Default: 45.",
        )
        
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and parse the activation but roll back database writes.",
        )
        
    def handle(self, *args, **options):
        base_url = normalize_base_url(options["base_url"])
        timeout = options["timeout"]
        
        dry_run = options["dry_run"]

        importer = CEMSActivationImporter(
            base_url=base_url,
            timeout=timeout,
            stdout=self.stdout,
            style=self.style,
        )

        failures: list[str] = []
        self.stdout.write(self.style.SUCCESS(importer))
        

        for raw_code in options["codes"]:
            code = raw_code.strip().upper()
            if not code:
                continue

            self.stdout.write(self.style.NOTICE(f"Fetching {code}..."))
            

            try:
                payload = importer.fetch_activation_payload(code)
                

                with transaction.atomic():
                    activation, stats = importer.import_activation_payload(
                        payload=payload,
                       
                    )

                    if dry_run:
                        transaction.set_rollback(True)

                suffix = " (dry run, rolled back)" if dry_run else ""
                self.stdout.write(
                    self.style.SUCCESS(
                        "Imported {code}: {aois} AOIs, {products} products, "
                        "{versions} versions, {images} images, {layers} layers{suffix}".format(
                            code=activation.code,
                            aois=stats["aois"],
                            products=stats["products"],
                            versions=stats["versions"],
                            images=stats["images"],
                            layers=stats["layers"],
                            suffix=suffix,
                        )
                    )
                )
            except Exception as exc:  # noqa: BLE001 - management command should report per-code failures.
                failures.append(f"{code}: {exc}")
                self.stderr.write(self.style.ERROR(f"Failed {code}: {exc}")) 

        if failures:
            raise CommandError("Some activations failed: " + "; ".join(failures))

        
        

    

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

        exact_matches = [item for item in results if str(item.get("code", "")).upper() == code.upper()]
        return exact_matches[0] if exact_matches else results[0]
    
    
    def import_activation_payload(self, *, payload: dict[str, Any], replace: bool = False):
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
                
                "activation_time": parse_api_datetime(get_any(payload, "activationTime", "activation_time")),
                "event_time": parse_api_datetime(get_any(payload, "eventTime", "event_time")),
                "registration_time": parse_api_datetime(get_any(payload, "registrationTime", "registration_time")),
                "publication_date": parse_api_datetime(get_any(payload, "publicationDate", "publication_date")),
                "last_update": parse_api_datetime(get_any(payload, "lastUpdate", "last_update")),
                
                "centroid": parse_point_wkt(get_any(payload, "centroid")),
                "extent": parse_polygonal_wkt(get_any(payload, "extent")),
            
                "infobulletins": get_any(payload, "infobulletins") or [],
                "closed": to_bool(get_any(payload, "closed"), default=False),
                "sensitive": to_bool(get_any(payload, "sensitive"), default=False),
                "raw_payload": payload,
            },
        )
        
        #self.stdout.write(self.style.NOTICE(f"Successfully Fetched {code}..."))
      # 2. Save the parent Activation and capture the response as a single variable 
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
        
        print('Whats up')
        
        aois_payload = get_any(payload, "aois", "aoi", "areasOfInterest") or []

        if isinstance(aois_payload, dict):
            aois_payload = [aois_payload]

        for aoi_payload in aois_payload:
            self.import_aoi(
                activation=activation,
                payload=aoi_payload,
            )
            stats["aois"] += 1

        return activation, stats

    def import_aoi(self, *, activation: CEMSRActivation, payload: dict[str, Any]):
        self.stdout.write(self.style.NOTICE(f"Saved AOIs {activation}"))
        aoi_number = to_int(get_any(payload, "number", "aoiNumber", "aoi_number"), default=0)
        extent = parse_polygonal_wkt(get_any(payload, "extent", "geom"))
        
        self.stdout.write(self.style.NOTICE(f"Saved AOIs {extent}"))
        defaults = filter_model_defaults(
            CEMSRAOI,
            {
                "aoi_name": get_any(payload, "name", "aoiName", "aoi_name") or f"AOI {aoi_number}",
                "blp_path": get_any(payload, "blpPath", "blp_path"),
                "is_real_extent": to_bool(get_any(payload, "isRealExtent", "is_real_extent"), default=True),
                "sqkm": to_decimal(get_any(payload, "sqkm", "sqKm", "areaSqKm", "area_sqkm")),
                "extent": extent,
                "geom": extent,
                "raw_payload": payload,
                
            },
        )

        aoi, _created = CEMSRAOI.objects.update_or_create(
            code=activation,
            aoi_number=aoi_number,
            defaults=defaults,
        )
        return aoi

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
    concrete_field_names = {
        field.name
        for field in model_cls._meta.get_fields()
        if getattr(field, "concrete", False) and not getattr(field, "auto_created", False)
    }
    return {key: value for key, value in defaults.items() if key in concrete_field_names}

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