"""
TurbopufferSearchService

Notes:
- This service provides async-first interfaces; underneath it uses the synchronous turbopuffer SDK
  by default, wrapped with `asyncio.to_thread` to avoid blocking the event loop
  (can be replaced if the SDK provides a native async client in the future)
- This service does not expose turbopuffer SDK model objects externally; return values are normalized
  to this module's schemas
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from src.infra.turbopuffer.config import TurbopufferConfig, turbopuffer_config
from src.infra.turbopuffer.exceptions import TurbopufferConfigError, map_external_exception
from src.infra.turbopuffer.schemas import (
    TurbopufferListNamespacesResponse,
    TurbopufferMultiQueryItem,
    TurbopufferMultiQueryResponse,
    TurbopufferNamespaceInfo,
    TurbopufferQueryResponse,
    TurbopufferRow,
    TurbopufferWriteResponse,
)

logger = logging.getLogger(__name__)


def _safe_row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "model_dump"):
        # pydantic v2
        return row.model_dump()
    if hasattr(row, "dict"):
        # pydantic v1
        return row.dict()
    if hasattr(row, "__dict__"):
        return dict(row.__dict__)
    return {}


def _normalize_rows(rows: Any) -> list[TurbopufferRow]:
    if rows is None:
        return []
    if not isinstance(rows, list):
        # Some SDK returns may be tuple/iterable
        try:
            rows = list(rows)
        except TypeError:
            rows = []

    out: list[TurbopufferRow] = []
    for r in rows:
        d = _safe_row_dict(r)
        if not d:
            continue

        row_id = d.get("id")
        if row_id is None:
            continue

        vector = d.get("vector")

        # turbopuffer responses commonly contain "$dist"; may also contain "dist"/"distance"
        dist = None
        for k in ("$dist", "dist", "distance"):
            if k in d:
                dist = d.get(k)
                break

        score = None
        for k in ("$score", "score"):
            if k in d:
                score = d.get(k)
                break

        attributes: dict[str, Any] = {}
        for k, v in d.items():
            if k in {"id", "vector", "$dist", "dist", "distance", "$score", "score"}:
                continue
            attributes[k] = v

        out.append(
            TurbopufferRow(
                id=row_id,
                vector=vector,
                distance=dist,
                score=score,
                attributes=attributes,
            )
        )
    return out


class TurbopufferSearchService:
    def __init__(
        self,
        config: TurbopufferConfig | None = None,
        *,
        client_factory: Callable[[TurbopufferConfig], Any] | None = None,
    ) -> None:
        self._config = config or turbopuffer_config
        self._client_factory = client_factory
        self._client: Any | None = None

    def _import_sdk(self) -> Any:
        try:
            import turbopuffer  # type: ignore
        except Exception as e:  # pragma: no cover
            raise TurbopufferConfigError("turbopuffer package is not available") from e
        return turbopuffer

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        if not self._config.configured:
            raise TurbopufferConfigError("TURBOPUFFER_API_KEY is not set")

        if self._client_factory is not None:
            self._client = self._client_factory(self._config)
            return self._client

        sdk = self._import_sdk()
        # Official example: turbopuffer.Turbopuffer(api_key=..., region=...)
        self._client = sdk.Turbopuffer(
            api_key=self._config.api_key, region=self._config.region
        )
        return self._client

    def _get_namespace(self, namespace: str) -> Any:
        client = self._get_client()

        # turbopuffer SDK commonly provides `.namespace(name)` returning a namespace object
        if hasattr(client, "namespace"):
            return client.namespace(namespace)

        # Compatible with stainless style: client.namespaces.<op>(namespace=..., **params)
        if hasattr(client, "namespaces"):
            return _NamespaceProxy(client.namespaces, namespace)

        raise TurbopufferConfigError("Invalid turbopuffer client: no namespace API")

    async def _call(self, fn: Callable[[], Any]) -> Any:
        task = asyncio.create_task(asyncio.to_thread(fn))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The SDK call is synchronous and cannot be stopped once its
            # thread begins. Keep the surrounding Project write lease alive
            # until the namespace mutation has physically completed.
            await task
            raise
        except Exception as e:
            mapped = map_external_exception(e)
            # Log more detailed error info for debugging
            error_body = None
            if hasattr(e, "body"):
                error_body = getattr(e, "body", None)
            elif hasattr(e, "response") and hasattr(e.response, "text"):
                error_body = e.response.text
            elif hasattr(e, "message"):
                error_body = getattr(e, "message", None)
            logger.warning(
                "Turbopuffer call failed: %s, details: %s",
                type(mapped).__name__,
                error_body or str(e)[:500],
            )
            raise mapped from e

    async def list_namespaces(
        self,
        *,
        prefix: str | None = None,
        cursor: str | None = None,
        page_size: int | None = None,
    ) -> TurbopufferListNamespacesResponse:
        """
        List namespaces (corresponds to GET /v1/namespaces).

        Notes:
        - SDK form may be iterator/paginator; here we do "loose adaptation" for stable schema output.
        - `next_cursor` will return None if the SDK doesn't expose it.
        """

        client = self._get_client()
        params: dict[str, Any] = {}
        if prefix is not None:
            params["prefix"] = prefix
        if cursor is not None:
            params["cursor"] = cursor
        if page_size is not None:
            params["page_size"] = page_size

        namespaces_attr = getattr(client, "namespaces", None)
        if callable(namespaces_attr):
            raw = await self._call(lambda: namespaces_attr(**params))
        elif namespaces_attr is not None and hasattr(namespaces_attr, "list"):
            raw = await self._call(lambda: namespaces_attr.list(**params))
        else:
            raise TurbopufferConfigError(
                "Invalid turbopuffer client: no namespaces API"
            )

        next_cursor = None
        if hasattr(raw, "next_cursor"):
            next_cursor = getattr(raw, "next_cursor")
        elif isinstance(raw, dict):
            next_cursor = raw.get("next_cursor")

        items: list[Any]
        if isinstance(raw, dict) and "namespaces" in raw:
            items = raw.get("namespaces") or []
        else:
            try:
                items = list(raw)
            except TypeError:
                items = []

        namespaces: list[TurbopufferNamespaceInfo] = []
        for it in items:
            if isinstance(it, dict):
                ns_id = it.get("id")
            else:
                ns_id = getattr(it, "id", None)
            if isinstance(ns_id, str) and ns_id:
                namespaces.append(TurbopufferNamespaceInfo(id=ns_id))

        return TurbopufferListNamespacesResponse(
            namespaces=namespaces, next_cursor=next_cursor
        )

    async def delete_namespace(self, namespace: str) -> None:
        """Delete an entire namespace (corresponds to Delete namespace API)."""
        ns = self._get_namespace(namespace)

        # Compatibility: some SDKs provide ns.delete()
        if hasattr(ns, "delete"):
            await self._call(lambda: ns.delete())
            return

        # Compatibility: old implementations/other SDKs may provide ns.delete_namespace()
        if hasattr(ns, "delete_namespace"):
            await self._call(lambda: getattr(ns, "delete_namespace")())
            return

        # turbopuffer==1.12.0: Namespace only provides delete_all() (clears data),
        # Deleting an entire namespace requires HTTP: DELETE /v2/namespaces/:namespace
        client = self._get_client()
        if hasattr(client, "delete"):
            await self._call(
                lambda: client.delete(
                    f"/v2/namespaces/{namespace}",
                    cast_to=object,
                )
            )
            return

        raise TurbopufferConfigError(
            "Invalid turbopuffer client: no delete namespace API"
        )

    async def delete_all(self, namespace: str) -> None:
        ns = self._get_namespace(namespace)
        await self._call(lambda: ns.delete_all())

    async def schema(self, namespace: str) -> Any:
        ns = self._get_namespace(namespace)
        return await self._call(lambda: ns.schema())

    async def update_schema(self, namespace: str, *, schema: dict[str, Any]) -> Any:
        ns = self._get_namespace(namespace)
        return await self._call(lambda: ns.update_schema(schema=schema))

    async def metadata(self, namespace: str) -> dict[str, Any]:
        """Get namespace metadata (corresponds to GET /v1/namespaces/:namespace/metadata)."""
        ns = self._get_namespace(namespace)
        raw = await self._call(lambda: ns.metadata())
        return _safe_row_dict(raw)

    async def hint_cache_warm(self, namespace: str) -> dict[str, Any]:
        """Hint turbopuffer to warm cache (corresponds to GET /v1/namespaces/:namespace/hint_cache_warm)."""
        ns = self._get_namespace(namespace)
        raw = await self._call(lambda: ns.hint_cache_warm())
        return _safe_row_dict(raw)

    async def recall(
        self,
        namespace: str,
        *,
        num: int | None = None,
        top_k: int | None = None,
        filters: Any | None = None,
        queries: list[list[float]] | None = None,
    ) -> dict[str, Any]:
        """Measure vector recall (corresponds to POST /v1/namespaces/:namespace/_debug/recall)."""
        ns = self._get_namespace(namespace)
        params: dict[str, Any] = {}
        if num is not None:
            params["num"] = num
        if top_k is not None:
            params["top_k"] = top_k
        if filters is not None:
            params["filters"] = filters
        if queries is not None:
            params["queries"] = queries
        raw = await self._call(lambda: ns.recall(**params))
        return _safe_row_dict(raw)

    async def write(
        self,
        namespace: str,
        *,
        upsert_rows: list[dict[str, Any]] | None = None,
        upsert_columns: dict[str, Any] | None = None,
        patch_rows: list[dict[str, Any]] | None = None,
        patch_columns: dict[str, Any] | None = None,
        deletes: list[int | str] | None = None,
        upsert_condition: Any | None = None,
        patch_condition: Any | None = None,
        delete_condition: Any | None = None,
        patch_by_filter: dict[str, Any] | None = None,
        patch_by_filter_allow_partial: bool | None = None,
        delete_by_filter: Any | None = None,
        delete_by_filter_allow_partial: bool | None = None,
        distance_metric: str | None = None,
        copy_from_namespace: Any | None = None,
        schema: dict[str, Any] | None = None,
        encryption: dict[str, Any] | None = None,
        disable_backpressure: bool | None = None,
    ) -> TurbopufferWriteResponse:
        ns = self._get_namespace(namespace)

        params: dict[str, Any] = {}
        if upsert_rows is not None:
            params["upsert_rows"] = upsert_rows
        if upsert_columns is not None:
            params["upsert_columns"] = upsert_columns
        if patch_rows is not None:
            params["patch_rows"] = patch_rows
        if patch_columns is not None:
            params["patch_columns"] = patch_columns
        if deletes is not None:
            params["deletes"] = deletes
        if upsert_condition is not None:
            params["upsert_condition"] = upsert_condition
        if patch_condition is not None:
            params["patch_condition"] = patch_condition
        if delete_condition is not None:
            params["delete_condition"] = delete_condition
        if patch_by_filter is not None:
            params["patch_by_filter"] = patch_by_filter
        if patch_by_filter_allow_partial is not None:
            params["patch_by_filter_allow_partial"] = patch_by_filter_allow_partial
        if delete_by_filter is not None:
            params["delete_by_filter"] = delete_by_filter
        if delete_by_filter_allow_partial is not None:
            params["delete_by_filter_allow_partial"] = delete_by_filter_allow_partial
        if distance_metric is not None:
            params["distance_metric"] = distance_metric
        if copy_from_namespace is not None:
            params["copy_from_namespace"] = copy_from_namespace
        if schema is not None:
            params["schema"] = schema
        if encryption is not None:
            params["encryption"] = encryption
        if disable_backpressure is not None:
            params["disable_backpressure"] = disable_backpressure

        raw = await self._call(lambda: ns.write(**params))
        d = _safe_row_dict(raw)
        return TurbopufferWriteResponse(
            rows_affected=d.get("rows_affected"),
            rows_upserted=d.get("rows_upserted"),
            rows_patched=d.get("rows_patched"),
            rows_deleted=d.get("rows_deleted"),
            rows_remaining=d.get("rows_remaining"),
            billing=d.get("billing"),
        )

    async def write_raw(
        self, namespace: str, payload: dict[str, Any]
    ) -> TurbopufferWriteResponse:
        """
        Pass-through write payload to SDK as-is (for advanced/debug use).

        Purpose:
        - Cover all write API parameters (including future additions), avoiding service layer lag.
        - Caller is responsible for payload; this method only does exception isolation and return normalization.
        """

        ns = self._get_namespace(namespace)
        raw = await self._call(lambda: ns.write(**payload))
        d = _safe_row_dict(raw)
        return TurbopufferWriteResponse(
            rows_affected=d.get("rows_affected"),
            rows_upserted=d.get("rows_upserted"),
            rows_patched=d.get("rows_patched"),
            rows_deleted=d.get("rows_deleted"),
            rows_remaining=d.get("rows_remaining"),
            billing=d.get("billing"),
        )

    async def query(
        self,
        namespace: str,
        *,
        rank_by: Any | None = None,
        top_k: int = 10,
        filters: Any | None = None,
        include_attributes: list[str] | bool | None = None,
        exclude_attributes: list[str] | None = None,
        limit: int | dict[str, Any] | None = None,
        aggregate_by: dict[str, Any] | None = None,
        group_by: list[str] | None = None,
        vector_encoding: str | None = None,
        consistency: dict[str, Any] | None = None,
    ) -> TurbopufferQueryResponse:
        ns = self._get_namespace(namespace)
        params: dict[str, Any] = {}
        # Note:
        # - rank_by query: top_k represents the number of documents returned (common alias for limit.total)
        # - aggregate_by without group_by (pure aggregation query): turbopuffer rejects top_k (400)
        if not (rank_by is None and aggregate_by is not None and group_by is None):
            params["top_k"] = top_k
        if rank_by is not None:
            params["rank_by"] = rank_by
        if filters is not None:
            params["filters"] = filters
        if include_attributes is not None:
            params["include_attributes"] = include_attributes
        if exclude_attributes is not None:
            params["exclude_attributes"] = exclude_attributes
        if limit is not None:
            params["limit"] = limit
        if aggregate_by is not None:
            params["aggregate_by"] = aggregate_by
        if group_by is not None:
            params["group_by"] = group_by
        if vector_encoding is not None:
            params["vector_encoding"] = vector_encoding
        if consistency is not None:
            params["consistency"] = consistency

        raw = await self._call(lambda: ns.query(**params))
        d = _safe_row_dict(raw)

        raw_rows = None
        if isinstance(raw, dict):
            raw_rows = raw.get("rows")
        elif hasattr(raw, "rows"):
            raw_rows = getattr(raw, "rows")
        else:
            raw_rows = raw

        return TurbopufferQueryResponse(
            rows=_normalize_rows(raw_rows),
            aggregations=d.get("aggregations"),
            aggregation_groups=d.get("aggregation_groups"),
            billing=d.get("billing"),
            performance=d.get("performance"),
        )

    async def query_raw(
        self, namespace: str, payload: dict[str, Any]
    ) -> TurbopufferQueryResponse:
        """
        Pass-through query payload to SDK as-is (for advanced/debug use).

        - Suitable for export / aggregation / diversity limit / consistency and other advanced parameter experiments
        - Returns are normalized as much as possible to this module's schemas (rows / aggregations / billing, etc.)
        """

        ns = self._get_namespace(namespace)
        raw = await self._call(lambda: ns.query(**payload))
        d = _safe_row_dict(raw)

        raw_rows = None
        if isinstance(raw, dict):
            raw_rows = raw.get("rows")
        elif hasattr(raw, "rows"):
            raw_rows = getattr(raw, "rows")
        else:
            raw_rows = raw

        return TurbopufferQueryResponse(
            rows=_normalize_rows(raw_rows),
            aggregations=d.get("aggregations"),
            aggregation_groups=d.get("aggregation_groups"),
            billing=d.get("billing"),
            performance=d.get("performance"),
        )

    async def multi_query(
        self,
        namespace: str,
        *,
        queries: list[dict[str, Any]],
        vector_encoding: str | None = None,
        consistency: dict[str, Any] | None = None,
    ) -> TurbopufferMultiQueryResponse:
        ns = self._get_namespace(namespace)
        params: dict[str, Any] = {"queries": queries}
        if vector_encoding is not None:
            params["vector_encoding"] = vector_encoding
        if consistency is not None:
            params["consistency"] = consistency
        raw = await self._call(lambda: ns.multi_query(**params))

        raw_results = None
        if isinstance(raw, dict):
            raw_results = raw.get("results") or raw.get("responses")
        elif hasattr(raw, "results"):
            raw_results = getattr(raw, "results")
        else:
            raw_results = raw

        results: list[TurbopufferMultiQueryItem] = []
        if raw_results is None:
            raw_results = []
        for item in raw_results:
            item_dict = _safe_row_dict(item)
            if isinstance(item, dict):
                rows = item.get("rows")
            elif hasattr(item, "rows"):
                rows = getattr(item, "rows")
            else:
                rows = item
            results.append(
                TurbopufferMultiQueryItem(
                    rows=_normalize_rows(rows),
                    aggregations=item_dict.get("aggregations"),
                    aggregation_groups=item_dict.get("aggregation_groups"),
                    billing=item_dict.get("billing"),
                    performance=item_dict.get("performance"),
                )
            )
        return TurbopufferMultiQueryResponse(results=results)

    async def multi_query_raw(
        self, namespace: str, payload: dict[str, Any]
    ) -> TurbopufferMultiQueryResponse:
        """
        Pass-through multi_query payload to SDK as-is (for advanced/debug use).

        Note: payload must contain a `queries` field.
        """

        ns = self._get_namespace(namespace)
        raw = await self._call(lambda: ns.multi_query(**payload))

        raw_results = None
        if isinstance(raw, dict):
            raw_results = raw.get("results") or raw.get("responses")
        elif hasattr(raw, "results"):
            raw_results = getattr(raw, "results")
        else:
            raw_results = raw

        results: list[TurbopufferMultiQueryItem] = []
        if raw_results is None:
            raw_results = []
        for item in raw_results:
            item_dict = _safe_row_dict(item)
            if isinstance(item, dict):
                rows = item.get("rows")
            elif hasattr(item, "rows"):
                rows = getattr(item, "rows")
            else:
                rows = item
            results.append(
                TurbopufferMultiQueryItem(
                    rows=_normalize_rows(rows),
                    aggregations=item_dict.get("aggregations"),
                    aggregation_groups=item_dict.get("aggregation_groups"),
                    billing=item_dict.get("billing"),
                    performance=item_dict.get("performance"),
                )
            )
        return TurbopufferMultiQueryResponse(results=results)


class _NamespaceProxy:
    """
    Adapt stainless-style `client.namespaces.<op>(namespace=..., **params)` to `ns.<op>(**params)`.
    """

    def __init__(self, namespaces_resource: Any, namespace: str) -> None:
        self._resource = namespaces_resource
        self._namespace = namespace

    def delete_all(self) -> Any:
        return self._resource.delete_all(namespace=self._namespace)

    def schema(self) -> Any:
        return self._resource.schema(namespace=self._namespace)

    def update_schema(self, *, schema: dict[str, Any]) -> Any:
        return self._resource.update_schema(namespace=self._namespace, schema=schema)

    def write(self, **params: Any) -> Any:
        return self._resource.write(namespace=self._namespace, **params)

    def query(self, **params: Any) -> Any:
        return self._resource.query(namespace=self._namespace, **params)

    def multi_query(self, **params: Any) -> Any:
        return self._resource.multi_query(namespace=self._namespace, **params)
