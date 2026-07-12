#python3 manage.py fetch_cemsr_activation EMSR886 EMSR885 EMSR667 EMSR790 EMSR877 EMSR881 EMSR882 EMSR884 EMSR887 EMSR888 EMSR889 EMSR890
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
from django.db import transaction

from proximity_alerts.services.cems_importer import (
    CEMSActivationImporter,
    CEMSImportError,
    normalize_base_url,
    
)
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


class Command(BaseCommand):
    help = "Fetch one Copernicus EMS Rapid Mapping activation in GeoDjango Models"

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
        self.stdout.write(self.style.SUCCESS("CEMS importer ready"))

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
            except Exception as exc:
                failures.append(f"{code}: {exc}")
                self.stderr.write(self.style.ERROR(f"Failed {code}: {exc}"))

        if failures:
            raise CommandError("Some activations failed: " + "; ".join(failures))
