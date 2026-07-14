# python3 manage.py fetch_cemsr_activation EMSR886 EMSR885 EMSR667 EMSR790 EMSR877 EMSR881 EMSR882 EMSR884 EMSR887 EMSR888 EMSR889 EMSR890
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
    normalize_base_url,

)
from proximity_alerts.models import CommandLog

logger = logging.getLogger(__name__)


DEFAULT_BASE_URL = "https://rapidmapping.emergency.copernicus.eu"
DETAIL_ENDPOINT = "/backend/dashboard-api/public-activations/"
LIST_ENDPOINT = "/backend/dashboard-api/public-activations-info/"



class CEMSImportError(Exception):
    """Raised when the remote API payload cannot fetch or import."""


class Command(BaseCommand):
    help = (
        "Fetch and Import specified Copernicus EMS Rapid Mapping activations. "
        "When no activation code is supplied, import the latest 10 activations."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "codes",
            nargs="*",
            help="Activation code(s), for example EMSR842 EMSR568. If omitted, the latest 10 activations are imported.",
        )
        parser.add_argument(
            "--base-url",
            default=getattr(
                settings, "CEMS_RAPID_MAPPING_BASE_URL", DEFAULT_BASE_URL),
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

    def get_latest_activation_codes(
        self,
        base_url: str,
        timeout: int,
    ) -> list[str]:
        url = urljoin(
            f"{base_url.rstrip('/')}/",
            f"{LIST_ENDPOINT.lstrip('/')}?limit=10",
        )

        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "django-cems-rapid-mapping-importer/1.0",
            },
        )

        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(
                response.read().decode("utf-8")
            )

        return [
            activation["code"]
            for activation in payload.get("results", [])
            if activation.get("code")
        ]

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

        codes = options["codes"]

        if not codes:
            self.stdout.write(
                self.style.NOTICE(
                    "No activation codes supplied. Fetching latest 10...")
            )
            codes = self.get_latest_activation_codes(base_url, timeout)

        for raw_code in codes:
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

        CommandLog.objects.create(command_name="CEMS Rapid Mapping API call")
        self.stdout.write(self.style.SUCCESS(
            "Successfully finished and logged timestamp."))

        if failures:
            raise CommandError(
                "Some activations failed: " + "; ".join(failures))
