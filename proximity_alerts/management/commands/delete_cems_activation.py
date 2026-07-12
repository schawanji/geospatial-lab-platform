# proximity_alerts/management/commands/delete_cems_activation.py
# python3 manage.py delete_cems_activation EMSR884 --cleanup-geoserver --drop-postgis-residuals
# python3 manage.py delete_cems_activation EMSR884 --cleanup-geoserver --drop-postgis-residuals --yes
# python3 manage.py delete_cems_activation EMSR886 EMSR885 EMSR667 EMSR790 EMSR877 EMSR881 EMSR882 EMSR884 EMSR887 EMSR888 EMSR889 EMSR890 EMSR891 EMSR892 --cleanup-geoserver --drop-postgis-residuals --prefix-fallback --yes
from django.core.management.base import BaseCommand, CommandError

from proximity_alerts.services.activations import (
    ActivationDeleteError,
    delete_activation_by_code,
)


class Command(BaseCommand):
    help = "Delete one or more CEMS Rapid Mapping activations and cleanup external resources."

    def add_arguments(self, parser):
        parser.add_argument(
            "codes",
            nargs="+",
            help="Activation code(s), for example EMSR881 EMSR842.",
        )

        parser.add_argument(
            "--yes",
            action="store_true",
            help="Actually delete the activation(s). Without this, nothing is deleted.",
        )

        parser.add_argument(
            "--cleanup-geoserver",
            action="store_true",
            help="Delete GeoServer layers/feature types/styles linked to the activation.",
        )

        parser.add_argument(
            "--drop-postgis-residuals",
            action="store_true",
            help="Drop generated PostGIS tables/views for the activation.",
        )

        parser.add_argument(
            "--prefix-fallback",
            action="store_true",
            help=(
                "Also delete generated PostGIS relations and GeoServer resources "
                "matching the activation prefix, useful if tracking rows are missing."
            ),
        )

    def handle(self, *args, **options):
        codes = options["codes"]
        confirmed = options["yes"]

        cleanup_geoserver = options["cleanup_geoserver"]
        drop_postgis_residuals = options["drop_postgis_residuals"]
        prefix_fallback = options["prefix_fallback"]

        if not confirmed:
            self.stdout.write(
                self.style.WARNING(
                    "Dry run only. Add --yes to actually delete."
                )
            )

        failures: list[str] = []

        for raw_code in codes:
            code = raw_code.strip().upper()

            if not code:
                continue

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
                self.stderr.write(self.style.ERROR(f"Failed {code}: {exc}"))
                continue

            if not confirmed:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Would delete activation {code}. Use --yes to confirm."
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
                self.stdout.write(f"  {model_name}: {count}")

            self.stdout.write(
                f"  GeoServer resources deleted: {result.geoserver_deleted_count}"
            )
            self.stdout.write(
                f"  PostGIS residuals dropped: {result.postgis_dropped_count}"
            )

        if failures:
            raise CommandError("Some deletions failed: " + "; ".join(failures))