#python3 manage.py delete_cems_activation EMSR886 EMSR885 EMSR667 EMSR790 EMSR877 EMSR881 EMSR882 EMSR884 EMSR887 EMSR888 --yes


from django.core.management.base import BaseCommand, CommandError

from proximity_alerts.services.activations import (
    ActivationDeleteError,
    delete_activation_by_code,
)


class Command(BaseCommand):
    help = "Delete one or more CEMS Rapid Mapping activations from the database."

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

    def handle(self, *args, **options):
        codes = options["codes"]
        confirmed = options["yes"]

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

            if not confirmed:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Would delete activation {code}. Use --yes to confirm."
                    )
                )
                continue

            try:
                result = delete_activation_by_code(code=code)
            except ActivationDeleteError as exc:
                failures.append(f"{code}: {exc}")
                self.stderr.write(self.style.ERROR(f"Failed {code}: {exc}"))
                continue

            self.stdout.write(
                self.style.SUCCESS(
                    f"Deleted {result.code}: "
                    f"{result.deleted_count} database row(s)"
                )
            )

            for model_name, count in result.deleted_details.items():
                self.stdout.write(f"  {model_name}: {count}")

        if failures:
            raise CommandError("Some deletions failed: " + "; ".join(failures))