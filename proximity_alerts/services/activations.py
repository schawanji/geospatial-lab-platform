from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection, transaction

from proximity_alerts.models import CEMSRActivation

from vector_tiles.models import CEMSRGeoServerLayer


class ActivationDeleteError(Exception):
    pass


@dataclass
class ActivationDeleteResult:
    code: str
    deleted_count: int
    deleted_details: dict[str, int]
    geoserver_deleted_count: int = 0
    postgis_dropped_count: int = 0


@dataclass
class GeoServerResource:
    workspace: str
    datastore: str | None
    geoserver_layer_name: str
    table_name: str | None
    style_name: str | None


def delete_activation_by_code(
    *,
    code: str,
    confirmed: bool = False,
    cleanup_geoserver: bool = False,
    drop_postgis_residuals: bool = False,
    prefix_fallback: bool = False,
    stdout=None,
    style=None,
) -> ActivationDeleteResult:
    code = code.strip().upper()

    try:
        activation = CEMSRActivation.objects.get(code=code)
    except CEMSRActivation.DoesNotExist as exc:
        raise ActivationDeleteError(f"Activation {code} does not exist.") from exc

    # IMPORTANT:
    # Collect these before activation.delete(), because cascade may delete tracking rows.
    geoserver_resources = collect_geoserver_resources(activation)

    postgis_relations = []

    if drop_postgis_residuals:
        postgis_relations.extend(
            collect_postgis_relations_from_geoserver_resources(geoserver_resources)
        )

    if prefix_fallback:
        postgis_relations.extend(
            find_generated_postgis_relations_for_code(code)
        )

    postgis_relations = deduplicate_relations(postgis_relations)

    if not confirmed:
        write(stdout, style, f"Would delete activation {code}", "NOTICE")
        write(stdout, style, f"GeoServer resources found: {len(geoserver_resources)}", "NOTICE")
        write(stdout, style, f"PostGIS residuals found: {len(postgis_relations)}", "NOTICE")

        for resource in geoserver_resources:
            write(
                stdout,
                style,
                f"  GeoServer: {resource.workspace}:{resource.geoserver_layer_name}",
                "NOTICE",
            )

        for schema, relation_name, relation_kind in postgis_relations:
            write(
                stdout,
                style,
                f"  PostGIS {relation_kind}: {schema}.{relation_name}",
                "NOTICE",
            )

        return ActivationDeleteResult(
            code=code,
            deleted_count=0,
            deleted_details={},
            geoserver_deleted_count=0,
            postgis_dropped_count=0,
        )

    geoserver_deleted_count = 0
    postgis_dropped_count = 0

    if cleanup_geoserver:
        client = GeoServerClient()

        for resource in geoserver_resources:
            geoserver_deleted_count += cleanup_geoserver_resource(
                client=client,
                resource=resource,
                stdout=stdout,
                style=style,
            )

        if prefix_fallback:
            geoserver_deleted_count += cleanup_geoserver_by_prefix(
                client=client,
                code=code,
                workspace=getattr(settings, "GEOSERVER_WORKSPACE", ""),
                stdout=stdout,
                style=style,
            )

    if drop_postgis_residuals:
        for schema, relation_name, relation_kind in postgis_relations:
            drop_postgis_relation(
                schema=schema,
                relation_name=relation_name,
                relation_kind=relation_kind,
            )
            postgis_dropped_count += 1
            write(
                stdout,
                style,
                f"Dropped PostGIS {relation_kind}: {schema}.{relation_name}",
                "SUCCESS",
            )

    with transaction.atomic():
        deleted_count, deleted_details = activation.delete()

    return ActivationDeleteResult(
        code=code,
        deleted_count=deleted_count,
        deleted_details=deleted_details,
        geoserver_deleted_count=geoserver_deleted_count,
        postgis_dropped_count=postgis_dropped_count,
    )


def collect_geoserver_resources(activation: CEMSRActivation) -> list[GeoServerResource]:
    layers = (
        CEMSRGeoServerLayer.objects
        .filter(product__aoi__activation=activation)
        .select_related("product", "product__aoi", "product__aoi__activation")
    )

    resources = []

    for layer in layers:
        resources.append(
            GeoServerResource(
                workspace=layer.workspace,
                datastore=layer.datastore,
                geoserver_layer_name=layer.geoserver_layer_name,
                table_name=layer.table_name,
                style_name=layer.style_name,
            )
        )

    return resources


def collect_postgis_relations_from_geoserver_resources(
    resources: list[GeoServerResource],
) -> list[tuple[str, str, str]]:
    schema = settings.GEOSERVER_POSTGIS.get("schema", "public")

    relations = []

    for resource in resources:
        if resource.table_name:
            relation_kind = get_relation_kind(schema, resource.table_name)

            if relation_kind:
                relations.append((schema, resource.table_name, relation_kind))

    return relations


def find_generated_postgis_relations_for_code(
    code: str,
) -> list[tuple[str, str, str]]:
    """
    Find generated CEMS tables/views by prefix.

    Example:
        cems_emsr884_%
    """
    prefix = f"cems_{code.lower()}_%"
    schema = settings.GEOSERVER_POSTGIS.get("schema", "public")

    sql = """
    SELECT
        n.nspname AS schema_name,
        c.relname AS relation_name,
        CASE c.relkind
            WHEN 'r' THEN 'table'
            WHEN 'v' THEN 'view'
            WHEN 'm' THEN 'materialized_view'
            ELSE c.relkind::text
        END AS relation_kind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = %s
      AND c.relname LIKE %s
      AND c.relkind IN ('r', 'v', 'm')
    ORDER BY c.relname;
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, [schema, prefix])
        rows = cursor.fetchall()

    return [(row[0], row[1], row[2]) for row in rows]


def get_relation_kind(schema: str, relation_name: str) -> str | None:
    sql = """
    SELECT
        CASE c.relkind
            WHEN 'r' THEN 'table'
            WHEN 'v' THEN 'view'
            WHEN 'm' THEN 'materialized_view'
            ELSE c.relkind::text
        END AS relation_kind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = %s
      AND c.relname = %s
      AND c.relkind IN ('r', 'v', 'm');
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, [schema, relation_name])
        row = cursor.fetchone()

    return row[0] if row else None


def deduplicate_relations(
    relations: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    seen = set()
    output = []

    for item in relations:
        key = (item[0], item[1])
        if key in seen:
            continue
        seen.add(key)
        output.append(item)

    return output


def drop_postgis_relation(
    *,
    schema: str,
    relation_name: str,
    relation_kind: str,
) -> None:
    if not is_safe_identifier(schema):
        raise ActivationDeleteError(f"Unsafe schema name: {schema}")

    if not is_safe_identifier(relation_name):
        raise ActivationDeleteError(f"Unsafe relation name: {relation_name}")

    if relation_kind == "view":
        sql = f'DROP VIEW IF EXISTS "{schema}"."{relation_name}" CASCADE;'
    elif relation_kind == "materialized_view":
        sql = f'DROP MATERIALIZED VIEW IF EXISTS "{schema}"."{relation_name}" CASCADE;'
    elif relation_kind == "table":
        sql = f'DROP TABLE IF EXISTS "{schema}"."{relation_name}" CASCADE;'
    else:
        raise ActivationDeleteError(f"Unsupported relation kind: {relation_kind}")

    with connection.cursor() as cursor:
        cursor.execute(sql)


def cleanup_geoserver_resource(
    *,
    client: "GeoServerClient",
    resource: GeoServerResource,
    stdout=None,
    style=None,
) -> int:
    deleted = 0

    if resource.datastore and resource.table_name:
        client.delete_featuretype(
            workspace=resource.workspace,
            datastore=resource.datastore,
            featuretype=resource.table_name,
        )
        deleted += 1
        write(
            stdout,
            style,
            f"Deleted GeoServer feature type: {resource.workspace}:{resource.table_name}",
            "SUCCESS",
        )
    else:
        client.delete_layer(
            workspace=resource.workspace,
            layer_name=resource.geoserver_layer_name,
        )
        deleted += 1
        write(
            stdout,
            style,
            f"Deleted GeoServer layer: {resource.workspace}:{resource.geoserver_layer_name}",
            "SUCCESS",
        )

    if resource.style_name:
        client.delete_style(
            workspace=resource.workspace,
            style_name=resource.style_name,
        )
        deleted += 1
        write(
            stdout,
            style,
            f"Deleted GeoServer style: {resource.workspace}:{resource.style_name}",
            "SUCCESS",
        )

    return deleted


def cleanup_geoserver_by_prefix(
    *,
    client: "GeoServerClient",
    code: str,
    workspace: str,
    stdout=None,
    style=None,
) -> int:
    """
    Fallback cleanup if tracking rows are gone.

    This tries to list GeoServer layers and delete ones containing cems_emsr884_.
    """
    if not workspace:
        return 0

    prefix = f"cems_{code.lower()}_"
    deleted = 0

    layer_names = client.list_layer_names(workspace=workspace)

    for layer_name in layer_names:
        simple_name = layer_name.split(":")[-1]

        if not simple_name.startswith(prefix):
            continue

        client.delete_layer(workspace=workspace, layer_name=simple_name)
        deleted += 1

        write(
            stdout,
            style,
            f"Deleted GeoServer layer by prefix: {workspace}:{simple_name}",
            "SUCCESS",
        )

    return deleted


class GeoServerClient:
    def __init__(self):
        self.base_url = settings.GEOSERVER_URL.rstrip("/")
        self.rest_url = f"{self.base_url}/rest"
        self.auth = (settings.GEOSERVER_USER, settings.GEOSERVER_PASSWORD)

    def request(self, method: str, path: str, **kwargs):
        url = f"{self.rest_url}{path}"

        response = requests.request(
            method,
            url,
            auth=self.auth,
            timeout=60,
            **kwargs,
        )

        if response.status_code == 404:
            return response

        if response.status_code >= 400:
            raise ActivationDeleteError(
                f"GeoServer REST error {response.status_code} for {method} {url}:\n"
                f"Response text: {response.text!r}"
            )

        return response

    def delete_featuretype(
        self,
        *,
        workspace: str,
        datastore: str,
        featuretype: str,
    ) -> None:
        encoded_workspace = quote(workspace)
        encoded_datastore = quote(datastore)
        encoded_featuretype = quote(featuretype)

        self.request(
            "DELETE",
            (
                f"/workspaces/{encoded_workspace}"
                f"/datastores/{encoded_datastore}"
                f"/featuretypes/{encoded_featuretype}.xml"
                f"?recurse=true&quietOnNotFound=true"
            ),
        )

    def delete_layer(
        self,
        *,
        workspace: str,
        layer_name: str,
    ) -> None:
        qualified_layer = quote(f"{workspace}:{layer_name}", safe=":")

        self.request(
            "DELETE",
            f"/layers/{qualified_layer}.xml?recurse=true&quietOnNotFound=true",
        )

    def delete_style(
        self,
        *,
        workspace: str,
        style_name: str,
    ) -> None:
        encoded_workspace = quote(workspace)
        encoded_style = quote(style_name)

        self.request(
            "DELETE",
            (
                f"/workspaces/{encoded_workspace}"
                f"/styles/{encoded_style}.xml"
                f"?recurse=true&quietOnNotFound=true"
            ),
        )
        
        from urllib.parse import quote


    def list_layer_names(
        self,
        workspace: str | None = None,
    ) -> list[str]:
        if workspace:
            encoded_workspace = quote(
                workspace,
                safe="",
            )

            endpoint = (
                f"/rest/workspaces/{encoded_workspace}/"
                "layers.json"
            )
        else:
            endpoint = "/rest/layers.json"

        data = self.request(
            "GET",
            endpoint,
        )

        if not isinstance(data, dict):
            return []

        layers_container = data.get("layers") or {}

        if not isinstance(layers_container, dict):
            return []

        layer_items = layers_container.get("layer") or []

        if not isinstance(layer_items, list):
            return []

        return [
            str(item["name"])
            for item in layer_items
            if isinstance(item, dict) and item.get("name")
        ]
            
    
def is_safe_identifier(value: str) -> bool:
    return bool(re.match(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$", value or ""))


def write(stdout, style, message: str, level: str = "NOTICE") -> None:
    if stdout is None:
        return

    if style is None:
        stdout.write(message)
        return

    if level == "SUCCESS":
        stdout.write(style.SUCCESS(message))
    elif level == "WARNING":
        stdout.write(style.WARNING(message))
    elif level == "ERROR":
        stdout.write(style.ERROR(message))
    else:
        stdout.write(style.NOTICE(message))