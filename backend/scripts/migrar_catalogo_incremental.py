"""Migra catálogo/ofertas/histórico entre bancos Preço Bão na mesma revisão.

DSNs vêm exclusivamente de PRECOBAO_MIGRATION_SOURCE_DSN e
PRECOBAO_MIGRATION_TARGET_DSN. O snapshot id deve ser o SHA-256 do dump imutável
da origem. Produção exige os dois flags explícitos documentados no --help.
"""

# Embedded SQL statements are intentionally kept on one line in query templates.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urlparse

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json, RealDictCursor

LOG = logging.getLogger("preco_bao.migration")
REVISION = "c42a83d1710e"
MIGRATION_SCHEMA = "preco_bao_migration"
CATALOG_COLUMNS = (
    "codigo_barras",
    "name_search",
    "principio_ativo",
    "laboratorio",
    "exige_receita",
    "criado_em",
    "atualizado_em",
    "categorias",
    "imagem_url",
    "imagem_fonte",
    "imagem_sku_origem",
    "imagem_ean",
    "imagem_match",
    "imagem_status",
    "imagem_verificada_em",
    "imagem_motivo_rejeicao",
    "categoria_regulatoria",
    "referencia_anvisa",
    "exige_receita_confirmada",
    "controle_especial",
    "classificacao_fonte",
    "imagem_atualizada_em",
    "imagem_falhas_consecutivas",
    "imagem_ultima_falha",
    "imagem_ultima_verificacao_em",
)
PHARMACY_COLUMNS = (
    "cnpj",
    "razao_social",
    "nome_fantasia",
    "telefone",
    "endereco_completo",
    "latitude",
    "longitude",
    "criado_em",
    "atualizado_em",
)
OFFER_COLUMNS = (
    "sku_interno",
    "preco",
    "quantidade_estoque",
    "disponivel",
    "url_origem",
    "imagem_url",
    "criado_em",
    "atualizado_em",
    "farmacia_id",
    "catalogo_id",
    "observado_em",
    "contexto_chave",
    "canal_venda",
    "contexto_local",
    "cep",
    "cidade",
    "uf",
    "loja_id",
    "loja_nome",
    "vendedor",
    "confiabilidade",
    "fonte_observado_em",
)
HISTORY_COLUMNS = (
    "medicamento_id",
    "farmacia_id",
    "oferta_id",
    "origem",
    "contexto_chave",
    "canal_venda",
    "contexto_local",
    "cep",
    "cidade",
    "uf",
    "loja_id",
    "loja_nome",
    "fonte_observado_em",
    "preco",
    "disponivel",
    "data_registro",
)
IMAGE_COLUMNS = {
    "imagem_url",
    "imagem_fonte",
    "imagem_sku_origem",
    "imagem_ean",
    "imagem_match",
    "imagem_status",
    "imagem_verificada_em",
    "imagem_motivo_rejeicao",
    "imagem_atualizada_em",
    "imagem_falhas_consecutivas",
    "imagem_ultima_falha",
    "imagem_ultima_verificacao_em",
}
AUTHORIZED_IMAGE_SOURCES = {"indiana"}


def normalized(value: Any) -> str:
    return " ".join((value or "").casefold().split())


def valid_http_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def image_source_authorized(source: str | None) -> bool:
    return (source or "").strip().casefold() in AUTHORIZED_IMAGE_SOURCES


def row_signature(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    data = {field: row.get(field) for field in fields}
    serialized = json.dumps(data, default=str, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def connect(dsn: str):
    return psycopg2.connect(
        dsn, connect_timeout=10, application_name="preco-bao-migrator"
    )


def dict_rows(cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def qname(schema: str, table: str):
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def ensure_target(
    conn,
    source_conn,
    target_schema: str,
    source_schema: str,
    snapshot_id: str,
    batch_size: int,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                sql.Identifier(MIGRATION_SCHEMA)
            )
        )
        cur.execute(
            sql.SQL("""
            CREATE TABLE IF NOT EXISTS {}.runs (
                snapshot_id text PRIMARY KEY, source_revision text NOT NULL,
                target_revision text NOT NULL, batch_size integer NOT NULL,
                started_at timestamptz NOT NULL DEFAULT now(),
                updated_at timestamptz NOT NULL DEFAULT now(),
                stats jsonb NOT NULL DEFAULT '{{}}'::jsonb, baseline jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                status text NOT NULL DEFAULT 'running'
            )
        """).format(sql.Identifier(MIGRATION_SCHEMA))
        )
        cur.execute(
            sql.SQL(
                "ALTER TABLE {}.runs ADD COLUMN IF NOT EXISTS baseline jsonb NOT NULL DEFAULT '{{}}'::jsonb"
            ).format(sql.Identifier(MIGRATION_SCHEMA))
        )
        for table, columns in (
            (
                "catalog_map",
                "source_id integer NOT NULL, target_id integer NOT NULL, status text NOT NULL, match_method text NOT NULL, source_signature text NOT NULL",
            ),
            (
                "pharmacy_map",
                "source_id integer NOT NULL, target_id integer NOT NULL, status text NOT NULL, source_signature text NOT NULL",
            ),
            (
                "offer_map",
                "source_id integer NOT NULL, target_id integer NOT NULL, status text NOT NULL, source_signature text NOT NULL",
            ),
            (
                "history_map",
                "source_id integer NOT NULL, target_id integer NOT NULL, status text NOT NULL, source_signature text NOT NULL",
            ),
        ):
            cur.execute(
                sql.SQL("""
                CREATE TABLE IF NOT EXISTS {}.{} (
                    snapshot_id text NOT NULL, {}, PRIMARY KEY(snapshot_id, source_id)
                )
            """).format(
                    sql.Identifier(MIGRATION_SCHEMA),
                    sql.Identifier(table),
                    sql.SQL(columns),
                )
            )
        cur.execute(
            sql.SQL("""
            CREATE TABLE IF NOT EXISTS {}.checkpoints (
                snapshot_id text NOT NULL, phase text NOT NULL, last_source_id integer NOT NULL DEFAULT 0,
                completed boolean NOT NULL DEFAULT false, updated_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(snapshot_id, phase)
            )
        """).format(sql.Identifier(MIGRATION_SCHEMA))
        )
        cur.execute(
            sql.SQL("""
            CREATE TABLE IF NOT EXISTS {}.conflicts (
                snapshot_id text NOT NULL, entity text NOT NULL, source_id integer NOT NULL,
                reason text NOT NULL, details jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                created_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(snapshot_id, entity, source_id)
            )
        """).format(sql.Identifier(MIGRATION_SCHEMA))
        )
        cur.execute(
            sql.SQL("""
            CREATE TABLE IF NOT EXISTS {}.catalog_attribute_differences (
                snapshot_id text NOT NULL, source_id integer NOT NULL, target_id integer NOT NULL,
                differences jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY(snapshot_id,source_id)
            )
        """).format(sql.Identifier(MIGRATION_SCHEMA))
        )
    conn.commit()

    source_revision = revision(source_conn, source_schema)
    target_revision = revision(conn, target_schema)
    if source_revision != REVISION or target_revision != REVISION:
        raise RuntimeError(
            f"schema mismatch: source={source_revision}, target={target_revision}, expected={REVISION}"
        )
    baseline = baseline_state(conn, target_schema)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("""
            INSERT INTO {}.runs(snapshot_id,source_revision,target_revision,batch_size,baseline)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT(snapshot_id) DO NOTHING
        """).format(sql.Identifier(MIGRATION_SCHEMA)),
            (snapshot_id, source_revision, target_revision, batch_size, Json(baseline)),
        )
        for phase in ("catalog", "pharmacies", "offers", "history"):
            cur.execute(
                sql.SQL("""
                INSERT INTO {}.checkpoints(snapshot_id,phase) VALUES (%s,%s)
                ON CONFLICT(snapshot_id,phase) DO NOTHING
            """).format(sql.Identifier(MIGRATION_SCHEMA)),
                (snapshot_id, phase),
            )
    conn.commit()


def revision(conn, schema: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT version_num FROM {}.alembic_version LIMIT 1").format(
                sql.Identifier(schema)
            )
        )
        row = cur.fetchone()
        return row[0] if row else None


def baseline_state(conn, schema: str) -> dict[str, Any]:
    """Hash user credential fields and favorite relations without exposing them."""
    result: dict[str, Any] = {}
    with conn.cursor() as cur:
        for table in (
            "catalogo_base",
            "farmacias",
            "ofertas_farmacia",
            "historico_precos",
            "usuario",
            "usuario_medicamento_favorito",
            "usuario_farmacia_favorita",
        ):
            cur.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            result[table + "_count"] = cur.fetchone()[0]
        cur.execute(
            sql.SQL("""
            SELECT id::text,email,hashed_password,nome,is_active,is_superuser,is_verified
            FROM {}.usuario ORDER BY id
        """).format(sql.Identifier(schema))
        )
        result["users_sha256"] = hashlib.sha256(
            json.dumps(cur.fetchall(), default=str, separators=(",", ":")).encode()
        ).hexdigest()
        for table in ("usuario_medicamento_favorito", "usuario_farmacia_favorita"):
            cur.execute(
                sql.SQL("SELECT * FROM {}.{} ORDER BY 1,2").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            result[table + "_sha256"] = hashlib.sha256(
                json.dumps(cur.fetchall(), default=str, separators=(",", ":")).encode()
            ).hexdigest()
    return result


def checkpoint(conn, snapshot_id: str, phase: str) -> tuple[int, bool]:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT last_source_id,completed FROM {}.checkpoints WHERE snapshot_id=%s AND phase=%s"
            ).format(sql.Identifier(MIGRATION_SCHEMA)),
            (snapshot_id, phase),
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(f"checkpoint ausente: {phase}")
        return row[0], row[1]


def set_checkpoint(
    cur, snapshot_id: str, phase: str, last_id: int, completed: bool = False
) -> None:
    cur.execute(
        sql.SQL("""
        UPDATE {}.checkpoints SET last_source_id=%s,completed=%s,updated_at=now()
        WHERE snapshot_id=%s AND phase=%s
    """).format(sql.Identifier(MIGRATION_SCHEMA)),
        (last_id, completed, snapshot_id, phase),
    )


def fetch_batch(
    conn, schema: str, table: str, columns: tuple[str, ...], last_id: int, limit: int
) -> list[dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL("SELECT id,{} FROM {} WHERE id>%s ORDER BY id LIMIT %s").format(
                sql.SQL(",").join(map(sql.Identifier, columns)), qname(schema, table)
            ),
            (last_id, limit),
        )
        return dict_rows(cur)


def insert_row(
    cur, schema: str, table: str, fields: tuple[str, ...], values: dict[str, Any]
) -> int:
    cols = sql.SQL(",").join(map(sql.Identifier, fields))
    params = [values.get(field) for field in fields]
    cur.execute(
        sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
            qname(schema, table),
            cols,
            sql.SQL(",").join(sql.Placeholder() for _ in fields),
        ),
        params,
    )
    inserted = cur.fetchone()
    return inserted["id"] if isinstance(inserted, dict) else inserted[0]


def persist_map(
    cur,
    table: str,
    snapshot_id: str,
    source_id: int,
    target_id: int,
    status: str,
    signature: str,
    match_method: str | None = None,
) -> None:
    if table == "catalog_map":
        cur.execute(
            sql.SQL("""
            INSERT INTO {}.catalog_map(snapshot_id,source_id,target_id,status,match_method,source_signature)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(snapshot_id,source_id) DO NOTHING
        """).format(sql.Identifier(MIGRATION_SCHEMA)),
            (snapshot_id, source_id, target_id, status, match_method, signature),
        )
    else:
        cur.execute(
            sql.SQL("""
            INSERT INTO {}.{}(snapshot_id,source_id,target_id,status,source_signature)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT(snapshot_id,source_id) DO NOTHING
        """).format(sql.Identifier(MIGRATION_SCHEMA), sql.Identifier(table)),
            (snapshot_id, source_id, target_id, status, signature),
        )


def existing_maps(conn, table: str, snapshot_id: str) -> dict[int, int]:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT source_id,target_id FROM {}.{} WHERE snapshot_id=%s"
            ).format(sql.Identifier(MIGRATION_SCHEMA), sql.Identifier(table)),
            (snapshot_id,),
        )
        return dict(cur.fetchall())


def log_conflict(
    cur,
    snapshot_id: str,
    entity: str,
    source_id: int,
    reason: str,
    details: dict[str, Any],
) -> None:
    cur.execute(
        sql.SQL("""
        INSERT INTO {}.conflicts(snapshot_id,entity,source_id,reason,details)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT(snapshot_id,entity,source_id)
        DO UPDATE SET reason=excluded.reason,details=excluded.details
    """).format(sql.Identifier(MIGRATION_SCHEMA)),
        (
            snapshot_id,
            entity,
            source_id,
            reason,
            Json(details, dumps=lambda o: json.dumps(o, default=str)),
        ),
    )


def catalog_index(conn, schema: str):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL(
                "SELECT id,codigo_barras,name_search,principio_ativo,laboratorio FROM {}.catalogo_base"
            ).format(sql.Identifier(schema))
        )
        rows = dict_rows(cur)
    by_ean: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_identity: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["codigo_barras"]:
            by_ean[row["codigo_barras"]].append(row)
        key = tuple(
            normalized(row[k])
            for k in ("name_search", "principio_ativo", "laboratorio")
        )
        by_identity[key].append(row)
    return by_ean, by_identity


def phase_catalog(
    source, target, source_schema, target_schema, snapshot_id, batch_size, stats
):
    last_id, done = checkpoint(target, snapshot_id, "catalog")
    if done:
        LOG.info("catalog checkpoint already complete")
        return
    mapped = existing_maps(target, "catalog_map", snapshot_id)
    by_ean, by_identity = catalog_index(target, target_schema)
    counters = Counter()
    while True:
        rows = fetch_batch(
            source, source_schema, "catalogo_base", CATALOG_COLUMNS, last_id, batch_size
        )
        if not rows:
            break
        with target.cursor(cursor_factory=RealDictCursor) as cur:
            for row in rows:
                sid = row["id"]
                last_id = sid
                if sid in mapped:
                    counters["already_mapped"] += 1
                    continue
                ean = row["codigo_barras"]
                found = by_ean.get(ean, []) if ean else []
                method = "ean"
                if len(found) > 1:
                    log_conflict(
                        cur,
                        snapshot_id,
                        "catalog",
                        sid,
                        "duplicate_target_ean",
                        {"ean": ean},
                    )
                    counters["conflict"] += 1
                    continue
                if found:
                    dst = found[0]
                    differences = {
                        k: {"source": row[k], "target": dst[k]}
                        for k in ("name_search", "principio_ativo", "laboratorio")
                        if normalized(row[k]) != normalized(dst[k])
                    }
                    if differences:
                        counters["ean_attribute_differences"] += 1
                        cur.execute(
                            sql.SQL("""
                            INSERT INTO {}.catalog_attribute_differences
                                (snapshot_id,source_id,target_id,differences)
                            VALUES (%s,%s,%s,%s)
                            ON CONFLICT(snapshot_id,source_id) DO UPDATE SET
                                target_id=excluded.target_id,differences=excluded.differences
                        """).format(sql.Identifier(MIGRATION_SCHEMA)),
                            (snapshot_id, sid, dst["id"], Json(differences)),
                        )
                    method = "ean"
                else:
                    identity = tuple(
                        normalized(row[k])
                        for k in ("name_search", "principio_ativo", "laboratorio")
                    )
                    candidates = by_identity.get(identity, [])
                    if len(candidates) > 1:
                        log_conflict(
                            cur,
                            snapshot_id,
                            "catalog",
                            sid,
                            "ambiguous_identity",
                            {
                                "candidate_ids": [x["id"] for x in candidates],
                                "ean": ean,
                            },
                        )
                        counters["conflict"] += 1
                        continue
                    if candidates:
                        candidate = candidates[0]
                        if (
                            ean
                            and candidate["codigo_barras"]
                            and ean != candidate["codigo_barras"]
                        ):
                            log_conflict(
                                cur,
                                snapshot_id,
                                "catalog",
                                sid,
                                "different_ean",
                                {
                                    "target_id": candidate["id"],
                                    "source_ean": ean,
                                    "target_ean": candidate["codigo_barras"],
                                },
                            )
                            counters["conflict"] += 1
                            continue
                        dst = candidate
                        method = "exact_identity"
                    else:
                        new_values = {
                            field: row.get(field) for field in CATALOG_COLUMNS
                        }
                        image_url = new_values.get("imagem_url")
                        verified = new_values.get(
                            "imagem_status"
                        ) == "verificada" and valid_http_url(image_url)
                        if not verified or not image_source_authorized(
                            new_values.get("imagem_fonte")
                        ):
                            if verified:
                                counters["verified_images_pending_authorization"] += 1
                            new_values["imagem_url"] = None
                        target_id = insert_row(
                            cur,
                            target_schema,
                            "catalogo_base",
                            CATALOG_COLUMNS,
                            new_values,
                        )
                        dst = {"id": target_id, **new_values}
                        counters["inserted"] += 1
                        if dst.get("codigo_barras"):
                            by_ean[dst["codigo_barras"]].append(dst)
                        key = tuple(
                            normalized(dst[k])
                            for k in ("name_search", "principio_ativo", "laboratorio")
                        )
                        by_identity[key].append(dst)
                        method = "new"
                if (
                    method != "new"
                    and row.get("imagem_status") == "verificada"
                    and valid_http_url(row.get("imagem_url"))
                    and image_source_authorized(row.get("imagem_fonte"))
                ):
                    image_fields = tuple(
                        c for c in CATALOG_COLUMNS if c in IMAGE_COLUMNS
                    )
                    cur.execute(
                        sql.SQL(
                            "UPDATE {}.catalogo_base SET {} WHERE id=%s AND imagem_url IS NULL"
                        ).format(
                            sql.Identifier(target_schema),
                            sql.SQL(",").join(
                                sql.SQL("{}=%s").format(sql.Identifier(c))
                                for c in image_fields
                            ),
                        ),
                        tuple(row.get(c) for c in image_fields) + (dst["id"],),
                    )
                    if cur.rowcount:
                        counters["verified_images_filled"] += 1
                elif (
                    row.get("imagem_status") == "verificada"
                    and valid_http_url(row.get("imagem_url"))
                    and not image_source_authorized(row.get("imagem_fonte"))
                ):
                    counters["verified_images_pending_authorization"] += 1
                persist_map(
                    cur,
                    "catalog_map",
                    snapshot_id,
                    sid,
                    dst["id"],
                    "inserted" if method == "new" else "matched",
                    row_signature(row, CATALOG_COLUMNS),
                    method,
                )
                mapped[sid] = dst["id"]
                counters["matched"] += method != "new"
            counters["source_rows"] += len(rows)
            stats["catalog"] = dict(counters)
            cur.execute(
                sql.SQL(
                    "UPDATE {}.runs SET stats=%s,updated_at=now() WHERE snapshot_id=%s"
                ).format(sql.Identifier(MIGRATION_SCHEMA)),
                (Json(stats), snapshot_id),
            )
            set_checkpoint(cur, snapshot_id, "catalog", last_id)
        target.commit()
        LOG.info("catalog batch through source id %s: %s", last_id, dict(counters))
    with target.cursor() as cur:
        set_checkpoint(cur, snapshot_id, "catalog", last_id, True)
    target.commit()
    stats["catalog"] = dict(counters)


def phase_pharmacies(
    source, target, source_schema, target_schema, snapshot_id, batch_size, stats
):
    last_id, done = checkpoint(target, snapshot_id, "pharmacies")
    if done:
        return
    mapped = existing_maps(target, "pharmacy_map", snapshot_id)
    with target.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT id,cnpj FROM {}.farmacias").format(
                sql.Identifier(target_schema)
            )
        )
        targets = {r[1]: r[0] for r in cur.fetchall()}
    counters = Counter()
    while True:
        rows = fetch_batch(
            source, source_schema, "farmacias", PHARMACY_COLUMNS, last_id, batch_size
        )
        if not rows:
            break
        with target.cursor() as cur:
            for row in rows:
                sid = row["id"]
                last_id = sid
                if sid in mapped:
                    counters["already_mapped"] += 1
                    continue
                cnpj = row["cnpj"]
                if cnpj in targets:
                    target_id = targets[cnpj]
                    status = "matched"
                    counters["matched"] += 1
                else:
                    target_id = insert_row(
                        cur, target_schema, "farmacias", PHARMACY_COLUMNS, row
                    )
                    targets[cnpj] = target_id
                    status = "inserted"
                    counters["inserted"] += 1
                persist_map(
                    cur,
                    "pharmacy_map",
                    snapshot_id,
                    sid,
                    target_id,
                    status,
                    row_signature(row, PHARMACY_COLUMNS),
                )
                mapped[sid] = target_id
            counters["source_rows"] += len(rows)
            stats["pharmacies"] = dict(counters)
            cur.execute(
                sql.SQL(
                    "UPDATE {}.runs SET stats=%s,updated_at=now() WHERE snapshot_id=%s"
                ).format(sql.Identifier(MIGRATION_SCHEMA)),
                (Json(stats), snapshot_id),
            )
            set_checkpoint(cur, snapshot_id, "pharmacies", last_id)
        target.commit()
        LOG.info("pharmacy batch through source id %s: %s", last_id, dict(counters))
    with target.cursor() as cur:
        set_checkpoint(cur, snapshot_id, "pharmacies", last_id, True)
    target.commit()
    stats["pharmacies"] = dict(counters)


def mapped_id(cur, table: str, snapshot_id: str, source_id: int) -> int | None:
    cur.execute(
        sql.SQL(
            "SELECT target_id FROM {}.{} WHERE snapshot_id=%s AND source_id=%s"
        ).format(sql.Identifier(MIGRATION_SCHEMA), sql.Identifier(table)),
        (snapshot_id, source_id),
    )
    row = cur.fetchone()
    return row[0] if row else None


def phase_offers(
    source, target, source_schema, target_schema, snapshot_id, batch_size, stats
):
    last_id, done = checkpoint(target, snapshot_id, "offers")
    if done:
        return
    mapped = existing_maps(target, "offer_map", snapshot_id)
    with source.cursor() as source_cur:
        source_cur.execute(
            sql.SQL("SELECT id,nome_fantasia FROM {}.farmacias").format(
                sql.Identifier(source_schema)
            )
        )
        source_pharmacy_names = dict(source_cur.fetchall())
    counters = Counter()
    while True:
        rows = fetch_batch(
            source,
            source_schema,
            "ofertas_farmacia",
            OFFER_COLUMNS,
            last_id,
            batch_size,
        )
        if not rows:
            break
        with target.cursor() as cur:
            for row in rows:
                sid = row["id"]
                last_id = sid
                if sid in mapped:
                    counters["already_mapped"] += 1
                    continue
                catalog_id = mapped_id(
                    cur, "catalog_map", snapshot_id, row["catalogo_id"]
                )
                pharmacy_id = mapped_id(
                    cur, "pharmacy_map", snapshot_id, row["farmacia_id"]
                )
                if catalog_id is None or pharmacy_id is None:
                    log_conflict(
                        cur,
                        snapshot_id,
                        "offer",
                        sid,
                        "unresolved_catalog_or_pharmacy",
                        {
                            "catalog_source_id": row["catalogo_id"],
                            "pharmacy_source_id": row["farmacia_id"],
                        },
                    )
                    counters["conflict_unmapped"] += 1
                    continue
                if not valid_http_url(row["url_origem"]):
                    log_conflict(
                        cur,
                        snapshot_id,
                        "offer",
                        sid,
                        "invalid_url",
                        {"url": row["url_origem"]},
                    )
                    counters["conflict_invalid_url"] += 1
                    continue
                source_pharmacy_name = source_pharmacy_names.get(row["farmacia_id"], "")
                allow_offer_image = image_source_authorized(source_pharmacy_name)
                if row.get("imagem_url") and not allow_offer_image:
                    counters["offer_images_pending_authorization"] += 1
                cur.execute(
                    sql.SQL(
                        "SELECT id,farmacia_id,catalogo_id,observado_em FROM {}.ofertas_farmacia WHERE url_origem=%s AND contexto_chave=%s"
                    ).format(sql.Identifier(target_schema)),
                    (row["url_origem"], row["contexto_chave"]),
                )
                existing = cur.fetchone()
                if existing:
                    (
                        target_id,
                        existing_pharmacy,
                        existing_catalog,
                        existing_observed,
                    ) = existing
                    if (
                        existing_pharmacy != pharmacy_id
                        or existing_catalog != catalog_id
                    ):
                        log_conflict(
                            cur,
                            snapshot_id,
                            "offer",
                            sid,
                            "unique_key_points_to_different_entity",
                            {
                                "target_offer_id": target_id,
                                "target_pharmacy_id": existing_pharmacy,
                                "target_catalog_id": existing_catalog,
                            },
                        )
                        counters["conflict_unique_key"] += 1
                        continue
                    status = "reconciled"
                    if row["observado_em"] and (
                        existing_observed is None
                        or row["observado_em"] > existing_observed
                    ):
                        mutable = [
                            "sku_interno",
                            "preco",
                            "quantidade_estoque",
                            "disponivel",
                            "atualizado_em",
                            "observado_em",
                            "canal_venda",
                            "contexto_local",
                            "cep",
                            "cidade",
                            "uf",
                            "loja_id",
                            "loja_nome",
                            "vendedor",
                            "confiabilidade",
                            "fonte_observado_em",
                        ]
                        if allow_offer_image:
                            mutable.append("imagem_url")
                        cur.execute(
                            sql.SQL(
                                "UPDATE {}.ofertas_farmacia SET {} "
                                "WHERE id=%s AND (observado_em IS NULL OR observado_em<%s)"
                            ).format(
                                sql.Identifier(target_schema),
                                sql.SQL(",").join(
                                    sql.SQL("{}=%s").format(sql.Identifier(c))
                                    for c in mutable
                                ),
                            ),
                            tuple(row[c] for c in mutable)
                            + (target_id, row["observado_em"]),
                        )
                        status = "updated_newer_source"
                        counters["updated_newer"] += 1
                        counters[f"updated_newer_pharmacy_{row['farmacia_id']}"] += 1
                    else:
                        counters["reconciled"] += 1
                        counters[f"reconciled_pharmacy_{row['farmacia_id']}"] += 1
                else:
                    values = dict(row)
                    values["farmacia_id"] = pharmacy_id
                    values["catalogo_id"] = catalog_id
                    if not allow_offer_image:
                        values["imagem_url"] = None
                    target_id = insert_row(
                        cur, target_schema, "ofertas_farmacia", OFFER_COLUMNS, values
                    )
                    status = "inserted"
                    counters["inserted"] += 1
                    counters[f"inserted_pharmacy_{row['farmacia_id']}"] += 1
                persist_map(
                    cur,
                    "offer_map",
                    snapshot_id,
                    sid,
                    target_id,
                    status,
                    row_signature(row, OFFER_COLUMNS),
                )
                mapped[sid] = target_id
            counters["source_rows"] += len(rows)
            stats["offers"] = dict(counters)
            cur.execute(
                sql.SQL(
                    "UPDATE {}.runs SET stats=%s,updated_at=now() WHERE snapshot_id=%s"
                ).format(sql.Identifier(MIGRATION_SCHEMA)),
                (Json(stats), snapshot_id),
            )
            set_checkpoint(cur, snapshot_id, "offers", last_id)
        target.commit()
        LOG.info("offer batch through source id %s: %s", last_id, dict(counters))
    with target.cursor() as cur:
        set_checkpoint(cur, snapshot_id, "offers", last_id, True)
    target.commit()
    stats["offers"] = dict(counters)


def history_values(
    row: dict[str, Any], catalog_id: int, pharmacy_id: int, offer_id: int | None
) -> dict[str, Any]:
    result = {field: row.get(field) for field in HISTORY_COLUMNS}
    result.update(
        medicamento_id=catalog_id, farmacia_id=pharmacy_id, oferta_id=offer_id
    )
    return result


def history_equal(cur, target_schema: str, values: dict[str, Any]) -> int | None:
    fields = tuple(
        c for c in HISTORY_COLUMNS if c not in ("medicamento_id", "farmacia_id")
    )
    predicates = [
        sql.SQL("{} IS NOT DISTINCT FROM %s").format(sql.Identifier(c)) for c in fields
    ]
    query = sql.SQL(
        "SELECT id FROM {}.historico_precos WHERE medicamento_id=%s AND farmacia_id=%s AND {} ORDER BY id LIMIT 1"
    )
    cur.execute(
        query.format(sql.Identifier(target_schema), sql.SQL(" AND ").join(predicates)),
        (values["medicamento_id"], values["farmacia_id"], *(values[c] for c in fields)),
    )
    row = cur.fetchone()
    return row[0] if row else None


def phase_history(
    source, target, source_schema, target_schema, snapshot_id, batch_size, stats
):
    last_id, done = checkpoint(target, snapshot_id, "history")
    if done:
        return
    mapped = existing_maps(target, "history_map", snapshot_id)
    counters = Counter()
    while True:
        rows = fetch_batch(
            source,
            source_schema,
            "historico_precos",
            HISTORY_COLUMNS,
            last_id,
            batch_size,
        )
        if not rows:
            break
        with target.cursor() as cur:
            for row in rows:
                sid = row["id"]
                last_id = sid
                if sid in mapped:
                    counters["already_mapped"] += 1
                    continue
                catalog_id = mapped_id(
                    cur, "catalog_map", snapshot_id, row["medicamento_id"]
                )
                pharmacy_id = mapped_id(
                    cur, "pharmacy_map", snapshot_id, row["farmacia_id"]
                )
                offer_id = (
                    mapped_id(cur, "offer_map", snapshot_id, row["oferta_id"])
                    if row["oferta_id"] is not None
                    else None
                )
                if (
                    catalog_id is None
                    or pharmacy_id is None
                    or (row["oferta_id"] is not None and offer_id is None)
                ):
                    log_conflict(
                        cur,
                        snapshot_id,
                        "history",
                        sid,
                        "unresolved_foreign_key",
                        {
                            "catalog_source_id": row["medicamento_id"],
                            "pharmacy_source_id": row["farmacia_id"],
                            "offer_source_id": row["oferta_id"],
                        },
                    )
                    counters["conflict_unmapped"] += 1
                    counters[f"conflict_pharmacy_{row['farmacia_id']}"] += 1
                    continue
                values = history_values(row, catalog_id, pharmacy_id, offer_id)
                target_id = history_equal(cur, target_schema, values)
                if target_id is not None:
                    status = "duplicate_exact_event"
                    counters["duplicate_exact"] += 1
                    counters[f"duplicate_pharmacy_{row['farmacia_id']}"] += 1
                else:
                    target_id = insert_row(
                        cur, target_schema, "historico_precos", HISTORY_COLUMNS, values
                    )
                    status = "inserted"
                    counters["inserted"] += 1
                    counters[f"inserted_pharmacy_{row['farmacia_id']}"] += 1
                persist_map(
                    cur,
                    "history_map",
                    snapshot_id,
                    sid,
                    target_id,
                    status,
                    row_signature(row, HISTORY_COLUMNS),
                )
                mapped[sid] = target_id
            counters["source_rows"] += len(rows)
            stats["history"] = dict(counters)
            cur.execute(
                sql.SQL(
                    "UPDATE {}.runs SET stats=%s,updated_at=now() WHERE snapshot_id=%s"
                ).format(sql.Identifier(MIGRATION_SCHEMA)),
                (Json(stats), snapshot_id),
            )
            set_checkpoint(cur, snapshot_id, "history", last_id)
        target.commit()
        LOG.info("history batch through source id %s: %s", last_id, dict(counters))
    with target.cursor() as cur:
        set_checkpoint(cur, snapshot_id, "history", last_id, True)
    target.commit()
    stats["history"] = dict(counters)


def validate_integrity(conn, schema: str) -> dict[str, int]:
    queries = {
        "orphan_offers": "SELECT count(*) FROM {s}.ofertas_farmacia o LEFT JOIN {s}.catalogo_base c ON c.id=o.catalogo_id LEFT JOIN {s}.farmacias f ON f.id=o.farmacia_id WHERE c.id IS NULL OR f.id IS NULL",
        "orphan_history_catalog_or_pharmacy": "SELECT count(*) FROM {s}.historico_precos h LEFT JOIN {s}.catalogo_base c ON c.id=h.medicamento_id LEFT JOIN {s}.farmacias f ON f.id=h.farmacia_id WHERE c.id IS NULL OR f.id IS NULL",
        "orphan_history_offer": "SELECT count(*) FROM {s}.historico_precos h LEFT JOIN {s}.ofertas_farmacia o ON o.id=h.oferta_id WHERE h.oferta_id IS NOT NULL AND o.id IS NULL",
        "duplicate_offer_keys": "SELECT count(*) FROM (SELECT url_origem,contexto_chave FROM {s}.ofertas_farmacia GROUP BY 1,2 HAVING count(*)>1) d",
    }
    result = {}
    with conn.cursor() as cur:
        for name, query in queries.items():
            cur.execute(sql.SQL(query).format(s=sql.Identifier(schema)))
            result[name] = cur.fetchone()[0]
        for table in (
            "catalogo_base",
            "farmacias",
            "ofertas_farmacia",
            "historico_precos",
            "usuario",
            "usuario_medicamento_favorito",
        ):
            cur.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            )
            result[table] = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM pg_constraint WHERE connamespace=%s::regnamespace "
            "AND contype='f'",
            (schema,),
        )
        result["foreign_key_constraints"] = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM pg_constraint WHERE connamespace=%s::regnamespace "
            "AND contype='u'",
            (schema,),
        )
        result["unique_constraints"] = cur.fetchone()[0]
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE connamespace=%s::regnamespace "
            "AND conname='uq_ofertas_farmacia_url_contexto' AND contype='u')",
            (schema,),
        )
        result["offer_unique_constraint"] = cur.fetchone()[0]
        for table, expected_index in (
            ("catalogo_base", "ix_catalogo_base_codigo_barras"),
            ("farmacias", "ix_farmacias_cnpj"),
        ):
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname=%s "
                "AND tablename=%s AND indexname=%s AND indexdef ILIKE 'CREATE UNIQUE%%')",
                (schema, table, expected_index),
            )
            result[f"{table}_unique_index"] = cur.fetchone()[0]
        for table in (
            "catalogo_base",
            "farmacias",
            "ofertas_farmacia",
            "historico_precos",
        ):
            cur.execute(
                sql.SQL(
                    "SELECT pg_get_serial_sequence(%s,'id'),max(id) FROM {}"
                ).format(qname(schema, table)),
                (f"{schema}.{table}",),
            )
            sequence, max_id = cur.fetchone()
            if sequence is None:
                result[f"{table}_sequence_ok"] = False
            else:
                cur.execute(
                    "SELECT last_value FROM pg_sequences "
                    "WHERE schemaname=%s AND sequencename="
                    "(SELECT c.relname FROM pg_class c "
                    "WHERE c.oid=pg_get_serial_sequence(%s,'id')::regclass)",
                    (schema, f"{schema}.{table}"),
                )
                sequence_row = cur.fetchone()
                result[f"{table}_sequence_ok"] = (
                    sequence_row is not None
                    and sequence_row[0] is not None
                    and sequence_row[0] >= (max_id or 0)
                )
    return result


def pharmacy_report(
    source, target, source_schema: str, snapshot_id: str
) -> dict[str, Any]:
    with source.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            sql.SQL("SELECT id,nome_fantasia FROM {}.farmacias ORDER BY id").format(
                sql.Identifier(source_schema)
            )
        )
        pharmacies = dict_rows(cur)
        cur.execute(
            sql.SQL("SELECT id,farmacia_id FROM {}.ofertas_farmacia").format(
                sql.Identifier(source_schema)
            )
        )
        source_offer_farmacy = {r["id"]: r["farmacia_id"] for r in cur.fetchall()}
        cur.execute(
            sql.SQL("SELECT id,farmacia_id FROM {}.historico_precos").format(
                sql.Identifier(source_schema)
            )
        )
        source_history_farmacy = {r["id"]: r["farmacia_id"] for r in cur.fetchall()}
    offer_status: dict[int, Counter] = defaultdict(Counter)
    history_status: dict[int, Counter] = defaultdict(Counter)
    with target.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT source_id,status FROM {}.offer_map WHERE snapshot_id=%s"
            ).format(sql.Identifier(MIGRATION_SCHEMA)),
            (snapshot_id,),
        )
        for source_id, status in cur.fetchall():
            offer_status[source_offer_farmacy[source_id]][status] += 1
        cur.execute(
            sql.SQL(
                "SELECT source_id,status FROM {}.history_map WHERE snapshot_id=%s"
            ).format(sql.Identifier(MIGRATION_SCHEMA)),
            (snapshot_id,),
        )
        for source_id, status in cur.fetchall():
            history_status[source_history_farmacy[source_id]][status] += 1
    result = {}
    for pharmacy in pharmacies:
        sid, name = pharmacy["id"], pharmacy["nome_fantasia"]
        offers = offer_status[sid]
        history = history_status[sid]
        offer_source_count = sum(
            source_offer_farmacy_id == sid
            for source_offer_farmacy_id in source_offer_farmacy.values()
        )
        history_source_count = sum(
            source_history_farmacy_id == sid
            for source_history_farmacy_id in source_history_farmacy.values()
        )
        offer_mapped = sum(offers.values())
        history_mapped = sum(history.values())
        result[name] = {
            "offers_source": offer_source_count,
            "offers_inserted": offers.get("inserted", 0),
            "offers_reconciled": offers.get("reconciled", 0)
            + offers.get("updated_newer_source", 0),
            "offers_pending": offer_source_count - offer_mapped,
            "history_source": history_source_count,
            "history_inserted": history.get("inserted", 0),
            "history_exact_duplicates": history.get("duplicate_exact_event", 0),
            "history_pending": history_source_count - history_mapped,
        }
    return result


def run(args) -> dict[str, Any]:
    source_dsn = os.environ.get("PRECOBAO_MIGRATION_SOURCE_DSN")
    target_dsn = os.environ.get("PRECOBAO_MIGRATION_TARGET_DSN")
    if not source_dsn or not target_dsn:
        raise RuntimeError(
            "Configure os DSNs nas variáveis de ambiente; não passe segredos na linha de comando."
        )
    if not re.fullmatch(r"[0-9a-f]{64}", args.snapshot_id):
        raise RuntimeError(
            "--snapshot-id precisa ser o SHA-256 hexadecimal do dump de origem."
        )
    source = connect(source_dsn)
    target = connect(target_dsn)
    try:
        with target.cursor() as cur:
            cur.execute("SELECT current_database(),inet_server_addr()::text")
            db_name, server_addr = cur.fetchone()
        if args.environment == "production":
            if args.production_confirmation != "WRITE-PRECO-BAO-PRODUCTION":
                raise RuntimeError(
                    "Produção exige confirmação explícita; nenhuma escrita realizada."
                )
            if "postgres.database.azure.com" not in target_dsn:
                raise RuntimeError(
                    "DSN não parece apontar para Azure PostgreSQL; abortando modo produção."
                )
        elif args.environment != "staging":
            raise RuntimeError("--environment deve ser staging ou production")
        if args.environment == "staging" and (
            "azure" in target_dsn.lower()
            or "postgres.database.azure.com" in target_dsn.lower()
        ):
            raise RuntimeError("DSN Azure rejeitado no modo staging")
        LOG.info(
            "target environment=%s database=%s server=%s",
            args.environment,
            db_name,
            server_addr,
        )
        if not args.apply:
            raise RuntimeError(
                "Use --apply para executar no staging isolado; dry run de produção não grava."
            )
        if (
            args.environment == "production"
            and args.production_confirmation != "WRITE-PRECO-BAO-PRODUCTION"
        ):
            raise RuntimeError("Confirmação de produção ausente")
        started = time.monotonic()
        ensure_target(
            target,
            source,
            args.target_schema,
            args.source_schema,
            args.snapshot_id,
            args.batch_size,
        )
        with target.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT status,stats,baseline,started_at FROM {}.runs WHERE snapshot_id=%s"
                ).format(sql.Identifier(MIGRATION_SCHEMA)),
                (args.snapshot_id,),
            )
            prior_status, saved_stats, baseline, started_at = cur.fetchone()
        saved_stats = saved_stats or {}
        stats: dict[str, Any] = (
            saved_stats.get("phases", {}) if prior_status == "complete" else saved_stats
        )
        with target.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_lock(hashtext('preco_bao_incremental_migration'))"
            )
        target.commit()
        for phase in (phase_catalog, phase_pharmacies, phase_offers, phase_history):
            phase(
                source,
                target,
                args.source_schema,
                args.target_schema,
                args.snapshot_id,
                args.batch_size,
                stats,
            )
        integrity = validate_integrity(target, args.target_schema)
        if any(
            integrity[k]
            for k in (
                "orphan_offers",
                "orphan_history_catalog_or_pharmacy",
                "orphan_history_offer",
                "duplicate_offer_keys",
            )
        ):
            raise RuntimeError(f"validação de integridade falhou: {integrity}")
        expected_integrity = (
            "offer_unique_constraint",
            "catalogo_base_unique_index",
            "farmacias_unique_index",
            "catalogo_base_sequence_ok",
            "farmacias_sequence_ok",
            "ofertas_farmacia_sequence_ok",
            "historico_precos_sequence_ok",
        )
        if any(not integrity.get(key) for key in expected_integrity):
            raise RuntimeError(f"constraints/índices/sequências inválidos: {integrity}")
        current_baseline = baseline_state(target, args.target_schema)
        invariant_fields = (
            "usuario_count",
            "usuario_medicamento_favorito_count",
            "usuario_farmacia_favorita_count",
            "users_sha256",
            "usuario_medicamento_favorito_sha256",
            "usuario_farmacia_favorita_sha256",
        )
        if any(
            baseline.get(key) != current_baseline.get(key) for key in invariant_fields
        ):
            raise RuntimeError(
                "usuários, hashes de credenciais ou favoritos mudaram durante a migração"
            )
        details = pharmacy_report(source, target, args.source_schema, args.snapshot_id)
        full_stats = {
            "phases": stats,
            "integrity": integrity,
            "by_pharmacy": details,
            "users_and_favorites_preserved": True,
        }
        with target.cursor() as cur:
            cur.execute(
                sql.SQL("""
                UPDATE {}.runs SET status='complete',stats=%s,updated_at=now()
                WHERE snapshot_id=%s
            """).format(sql.Identifier(MIGRATION_SCHEMA)),
                (Json(full_stats), args.snapshot_id),
            )
        target.commit()
        report = {
            "snapshot_id": args.snapshot_id,
            "environment": args.environment,
            "duration_seconds": round(time.monotonic() - started, 2),
            "resume_from_completed_snapshot": prior_status == "complete",
            "changes_this_execution": ({} if prior_status == "complete" else stats),
            "prior_migration_stats": stats,
            "initial_target_counts": {
                key: baseline.get(key)
                for key in (
                    "catalogo_base_count",
                    "farmacias_count",
                    "ofertas_farmacia_count",
                    "historico_precos_count",
                    "usuario_count",
                    "usuario_medicamento_favorito_count",
                )
            },
            "by_pharmacy": details,
            "integrity": integrity,
            "users_and_favorites_preserved": True,
        }
        LOG.info(
            "migration complete: %s", json.dumps(report, default=str, sort_keys=True)
        )
        return report
    except Exception:
        target.rollback()
        LOG.exception(
            "migration failed; current batch rolled back; prior checkpoints remain resumable"
        )
        raise
    finally:
        try:
            with target.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(hashtext('preco_bao_incremental_migration'))"
                )
            target.commit()
        except Exception:
            target.rollback()
        source.close()
        target.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-schema", default="public")
    parser.add_argument("--target-schema", default="preco_bao_app")
    parser.add_argument(
        "--snapshot-id",
        required=True,
        help="SHA-256 do dump consistente e imutável da origem",
    )
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument(
        "--environment", choices=("staging", "production"), required=True
    )
    parser.add_argument(
        "--apply", action="store_true", help="Executar as fases e persistir checkpoints"
    )
    parser.add_argument(
        "--production-confirmation",
        default=None,
        help="Token de confirmação operacional separado; não substitui autorização do responsável",
    )
    return parser.parse_args(argv)


def main(argv=None):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = parse_args(argv)
    if args.batch_size < 1 or args.batch_size > 2000:
        raise SystemExit("--batch-size deve estar entre 1 e 2000")
    try:
        result = run(args)
    except Exception as exc:
        LOG.error("migração não concluída: %s", exc)
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
