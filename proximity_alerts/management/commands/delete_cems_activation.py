# python3 manage.py delete_cems_activation EMSR884 EMSR885 --yes
# python3 manage.py delete_cems_activation --all

"""python3 manage.py delete_cems_activation \
    --all \
    --cleanup-geoserver \
    --drop-postgis-residuals \
    --prefix-fallback \
    --yes"""


from django.core.management.base import BaseCommand, CommandError

from proximity_alerts.models import CEMSRActivation
from proximity_alerts.services.activations import (
    ActivationDeleteError,
    delete_activation_by_code,
)


class Command(BaseCommand):
    help = (
        "Delete one or more CEMS Rapid Mapping activations "
        "and clean up external resources."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "codes",
            nargs="*",
            help=(
                "Optional activation code(s), for example "
                "EMSR881 EMSR842."
            ),
        )

        parser.add_argument(
            "--all",
            action="store_true",
            help="Delete every CEMS Rapid Mapping activation.",
        )

        parser.add_argument(
            "--yes",
            action="store_true",
            help=(
                "Actually delete the activation(s). "
                "Without this option, nothing is deleted."
            ),
        )

        parser.add_argument(
            "--cleanup-geoserver",
            action="store_true",
            help=(
                "Delete GeoServer layers, feature types and styles "
                "linked to each activation."
            ),
        )

        parser.add_argument(
            "--drop-postgis-residuals",
            action="store_true",
            help=(
                "Drop generated PostGIS tables and views "
                "for each activation."
            ),
        )

        parser.add_argument(
            "--prefix-fallback",
            action="store_true",
            help=(
                "Also delete generated PostGIS relations and "
                "GeoServer resources matching each activation prefix. "
                "Useful when tracking rows are missing."
            ),
        )

    def handle(self, *args, **options):
        raw_codes = options["codes"]
        delete_all = options["all"]
        confirmed = options["yes"]

        cleanup_geoserver = options["cleanup_geoserver"]
        drop_postgis_residuals = options["drop_postgis_residuals"]
        prefix_fallback = options["prefix_fallback"]

        if delete_all and raw_codes:
            raise CommandError(
                "Do not provide activation codes together with --all."
            )

        if delete_all:
            codes = list(
                CEMSRActivation.objects
                .order_by("code")
                .values_list("code", flat=True)
            )

            if not codes:
                self.stdout.write(
                    self.style.WARNING(
                        "No CEMS activations were found."
                    )
                )
                return

            self.stdout.write(
                self.style.WARNING(
                    f"--all selected: {len(codes)} activation(s) "
                    "will be processed."
                )
            )

        else:
            codes = self._normalise_codes(raw_codes)

            if not codes:
                raise CommandError(
                    "Provide at least one activation code or use --all."
                )

        if not confirmed:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run only. Add --yes to actually delete."
                )
            )

        failures: list[str] = []

        for code in codes:
            try:
                result = delete_activation_by_code(
                    code=code,
                    confirmed=confirmed,
                    cleanup_geoserver=cleanup_geoserver,
                    drop_postgis_residuals=drop_postgis_residuals,
                    prefix_fallback=prefix_fallback,
                    stdout=self.stdout,
                    style=self.style,
                )

            except ActivationDeleteError as exc:
                failures.append(f"{code}: {exc}")

                self.stderr.write(
                    self.style.ERROR(
                        f"Failed {code}: {exc}"
                    )
                )

                continue

            if not confirmed:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Would delete activation {code}. "
                        "Use --yes to confirm."
                    )
                )

                continue

            self.stdout.write(
                self.style.SUCCESS(
                    f"Deleted {result.code}: "
                    f"{result.deleted_count} Django database row(s)"
                )
            )

            for model_name, count in result.deleted_details.items():
                self.stdout.write(
                    f"  {model_name}: {count}"
                )

            self.stdout.write(
                "  GeoServer resources deleted: "
                f"{result.geoserver_deleted_count}"
            )

            self.stdout.write(
                "  PostGIS residuals dropped: "
                f"{result.postgis_dropped_count}"
            )

        if failures:
            raise CommandError(
                "Some deletions failed: "
                + "; ".join(failures)
            )

        if confirmed:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Finished deleting {len(codes)} activation(s)."
                )
            )

    @staticmethod
    def _normalise_codes(raw_codes: list[str]) -> list[str]:
        """
        Normalise codes and remove duplicates while preserving order.
        """
        codes: list[str] = []
        seen: set[str] = set()

        for raw_code in raw_codes:
            code = raw_code.strip().upper()

            if not code or code in seen:
                continue

            seen.add(code)
            codes.append(code)

        return codes