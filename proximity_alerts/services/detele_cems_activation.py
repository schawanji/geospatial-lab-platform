from django.shortcuts import get_object_or_404
from django.db import transaction

from proximity_alerts.models import CEMSRActivation


@transaction.atomic
def delete_activation_by_code(code: str) -> tuple[int, dict[str, int]]:
    """
    Delete one activation and all related records through CASCADE relationships.
    Returns Django's delete count and per-model delete details.
    """
    activation = get_object_or_404(CEMSRActivation, code=code)

    deleted_count, deleted_details = activation.delete()

    return deleted_count, deleted_details