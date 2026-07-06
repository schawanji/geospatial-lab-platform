from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from proximity_alerts.models import CEMSRActivation


class ActivationDeleteError(Exception):
    """Raised when an activation cannot be deleted."""


@dataclass
class ActivationDeleteResult:
    code: str
    deleted_count: int
    deleted_details: dict[str, int]


@transaction.atomic
def delete_activation_by_code(*, code: str) -> ActivationDeleteResult:
    """
    Delete one activation and its related objects through cascade.

    This assumes your related ForeignKeys use on_delete=models.CASCADE.
    """
    normalized_code = code.strip().upper()

    if not normalized_code:
        raise ActivationDeleteError("Activation code is required.")

    try:
        activation = CEMSRActivation.objects.get(code=normalized_code)
    except CEMSRActivation.DoesNotExist as exc:
        raise ActivationDeleteError(
            f"No activation found with code {normalized_code}."
        ) from exc

    deleted_count, deleted_details = activation.delete()

    return ActivationDeleteResult(
        code=normalized_code,
        deleted_count=deleted_count,
        deleted_details=deleted_details,
    )


def activation_exists(*, code: str) -> bool:
    return CEMSRActivation.objects.filter(code=code.strip().upper()).exists()