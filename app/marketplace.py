from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .control_plane import ARTIFACT_DIGEST_RE, ControlPlane, Principal
from .database import Database, json_dumps, utc_now
from .errors import AppError
from .repository import Repository, make_id
from .skill_engine import parse_skill_metadata


SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,58}[a-z0-9])?$")
CATEGORIES = {"productivity", "research", "writing", "coding", "data", "design", "operations", "other"}
LEADERBOARD_WINDOW_DAYS = 7


class MarketplaceService:
    def __init__(self, database: Database, repository: Repository, control_plane: ControlPlane):
        self.database = database
        self.repository = repository
        self.control_plane = control_plane

    def list_publications(self, query: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
        query = query or {}
        search = str((query.get("q") or [""])[0]).strip()[:100]
        category = str((query.get("category") or [""])[0]).strip()
        pricing = str((query.get("pricing") or [""])[0]).strip()
        clauses = ["p.status='published'"]
        values: list[Any] = []
        if search:
            clauses.append("(p.name LIKE ? OR p.summary LIKE ?)")
            values.extend([f"%{search}%", f"%{search}%"])
        if category in CATEGORIES:
            clauses.append("p.category=?")
            values.append(category)
        if pricing in {"free", "paid"}:
            clauses.append("p.pricing_type=?")
            values.append(pricing)
        with self.database.session() as connection:
            rows = connection.execute(
                f"""
                SELECT p.*, u.display_name AS publisher_name
                FROM marketplace_publications p JOIN users u ON u.id=p.publisher_user_id
                WHERE {' AND '.join(clauses)}
                ORDER BY p.average_rating DESC, p.review_count DESC, p.purchase_count DESC, p.published_at DESC
                LIMIT 100
                """,
                values,
            ).fetchall()
        return [self._public_publication(dict(row)) for row in rows]

    def leaderboard(self, limit: int = 10) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=LEADERBOARD_WINDOW_DAYS)).isoformat(timespec="milliseconds")
        with self.database.session() as connection:
            rows = connection.execute(
                """
                WITH recent_reviews AS (
                    SELECT publication_id,
                           COUNT(*) AS weekly_review_count,
                           AVG(rating) AS weekly_average_rating
                    FROM marketplace_reviews
                    WHERE status='published' AND created_at>=?
                    GROUP BY publication_id
                ),
                recent_orders AS (
                    SELECT publication_id, COUNT(*) AS weekly_purchase_count
                    FROM marketplace_orders
                    WHERE status='paid' AND created_at>=?
                    GROUP BY publication_id
                ),
                eligible AS (
                    SELECT p.*, u.display_name AS publisher_name,
                           COALESCE(rr.weekly_review_count, 0) AS weekly_review_count,
                           COALESCE(rr.weekly_average_rating, 0.0) AS weekly_average_rating,
                           COALESCE(ro.weekly_purchase_count, 0) AS weekly_purchase_count
                    FROM marketplace_publications p
                    JOIN users u ON u.id=p.publisher_user_id
                    LEFT JOIN recent_reviews rr ON rr.publication_id=p.id
                    LEFT JOIN recent_orders ro ON ro.publication_id=p.id
                    WHERE p.status='published'
                )
                SELECT eligible.*,
                       (CASE WHEN weekly_review_count>0
                             THEN ((((weekly_average_rating * weekly_review_count) + 19.0) / (weekly_review_count + 5.0)) * 20.0)
                             ELSE 0.0 END
                        + MIN(weekly_review_count, 50) * 1.1
                        + MIN(weekly_purchase_count, 100) * 0.25
                        + CASE WHEN curation_status='curated' THEN 8.0 ELSE 0.0 END) AS rank_score
                FROM eligible
                WHERE weekly_review_count + weekly_purchase_count > 0
                ORDER BY rank_score DESC, weekly_review_count DESC, weekly_purchase_count DESC, published_at DESC
                LIMIT ?
                """,
                (cutoff, cutoff, max(1, min(limit, 50))),
            ).fetchall()
        ranking = []
        for row in rows:
            record = dict(row)
            publication = self._public_publication(record)
            publication.update({
                "lifetime_average_rating": publication["average_rating"],
                "lifetime_review_count": publication["review_count"],
                "lifetime_purchase_count": publication["purchase_count"],
                "average_rating": round(float(record["weekly_average_rating"]), 2),
                "review_count": int(record["weekly_review_count"]),
                "purchase_count": int(record["weekly_purchase_count"]),
                "ranking_window_days": LEADERBOARD_WINDOW_DAYS,
            })
            ranking.append(publication)
        return ranking

    def get_publication(self, publication_id: str) -> dict[str, Any]:
        with self.database.session() as connection:
            row = connection.execute(
                """
                SELECT p.*, u.display_name AS publisher_name FROM marketplace_publications p
                JOIN users u ON u.id=p.publisher_user_id WHERE p.id=? AND p.status='published'
                """,
                (publication_id,),
            ).fetchone()
            if row is None:
                raise AppError("publication_not_found", "Skill 发布内容不存在。", 404)
            reviews = connection.execute(
                """
                SELECT r.id, r.rating, r.title, r.body, r.created_at, u.display_name AS reviewer_name
                FROM marketplace_reviews r JOIN users u ON u.id=r.reviewer_user_id
                WHERE r.publication_id=? AND r.status='published' ORDER BY r.created_at DESC LIMIT 50
                """,
                (publication_id,),
            ).fetchall()
            lifecycle = connection.execute(
                """
                SELECT v.version AS skill_version, v.status AS delivery_status, v.change_summary,
                       v.created_at AS version_created_at, pr.route, up.enabled AS updates_enabled,
                       up.frequency AS update_frequency, up.last_checked_at,
                       uc.status AS update_status, uc.created_at AS update_checked_at
                FROM marketplace_publications mp
                LEFT JOIN skill_versions v ON v.id=mp.skill_version_id
                LEFT JOIN projects pr ON pr.id=mp.project_id
                LEFT JOIN update_policies up ON up.project_id=mp.project_id
                LEFT JOIN update_checks uc ON uc.project_id=mp.project_id
                WHERE mp.id=? ORDER BY uc.created_at DESC LIMIT 1
                """,
                (publication_id,),
            ).fetchone()
            validations = []
            if row["skill_version_id"]:
                validations = connection.execute(
                    """
                    SELECT stage, status, score, findings_json, evidence_json, created_at
                    FROM validation_runs WHERE version_id=? ORDER BY created_at DESC
                    """,
                    (row["skill_version_id"],),
                ).fetchall()
        result = self._public_publication(dict(row))
        result["reviews"] = [dict(item) for item in reviews]
        result["trust_passport"] = self._trust_passport(dict(row), dict(lifecycle) if lifecycle else {}, [dict(item) for item in validations])
        return result

    def seed_curated_catalog(self, entries: list[dict[str, Any]]) -> None:
        """Seed honest, static-only sample evidence for the local demonstration profile."""
        if not entries:
            return
        tenant_id = "ten_curated_demo"
        publisher_id = "usr_curated_demo"
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO tenants(id,name,slug,plan,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (tenant_id, "SkillSentra Curated", "skillsentra-curated", "team", "active", now, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO users(
                    id,email,normalized_email,display_name,password_salt,password_hash,status,email_verified,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (publisher_id, "curated@skillsentra.invalid", "curated@skillsentra.invalid", "SkillSentra Curated",
                 "AAAAAAAAAAAAAAAAAAAAAA==", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=", "disabled", 1, now, now),
            )
            connection.execute(
                "INSERT OR IGNORE INTO tenant_memberships(tenant_id,user_id,roles_json,status,created_at) VALUES (?,?,?,?,?)",
                (tenant_id, publisher_id, '["creator"]', "active", now),
            )
            for entry in entries:
                root = Path(entry["root"])
                scan = dict(entry["scan"])
                metadata = parse_skill_metadata(root / "SKILL.md")
                name = metadata.get("name") or root.name
                slug = self._slug(name)
                publication_id = f"pub_curated_{slug.replace('-', '_')}"
                manifest = (scan.get("evidence") or {}).get("manifest") or {}
                trust_evidence = {
                    "stage": "static",
                    "status": scan.get("status", "unknown"),
                    "score": scan.get("score", 0),
                    "findings": scan.get("findings", []),
                    "evidence": scan.get("evidence", {}),
                    "created_at": now,
                }
                connection.execute(
                    """
                    INSERT OR IGNORE INTO marketplace_publications(
                        id,tenant_id,publisher_user_id,name,slug,summary,category,
                        canonicalization_version,digest_algorithm,artifact_digest,pricing_type,price_minor,currency,
                        status,published_at,created_at,updated_at,license_id,compatibility_json,permissions_json,
                        data_policy,support_policy,curation_status,trust_evidence_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'published',?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        publication_id, tenant_id, publisher_id, name, slug, metadata.get("description") or "Curated Skill sample",
                        "research" if "report" in name or "asset" in name else "productivity",
                        manifest.get("canonicalization_version", "artifact-canon-v1"),
                        manifest.get("digest_algorithm", "sha256"), manifest.get("artifact_digest", ""),
                        "free", 0, "USD", now, now, now, "unspecified",
                        json_dumps(["Markdown Skill hosts"]), json_dumps(["No external actions declared"]),
                        "not_declared", "community", "curated", json_dumps(trust_evidence),
                    ),
                )

    def creator_dashboard(self, principal: Principal) -> dict[str, Any]:
        principal.require("creator")
        with self.database.session() as connection:
            publications = connection.execute(
                "SELECT * FROM marketplace_publications WHERE publisher_user_id=? ORDER BY updated_at DESC",
                (principal.actor_id,),
            ).fetchall()
            projects = connection.execute(
                "SELECT * FROM projects WHERE tenant_id=? AND owner_user_id=? ORDER BY updated_at DESC",
                (principal.tenant_id, principal.actor_id),
            ).fetchall()
            versions = connection.execute(
                """
                SELECT v.* FROM skill_versions v JOIN projects p ON p.id=v.project_id
                LEFT JOIN marketplace_publications mp ON mp.skill_version_id=v.id
                WHERE p.tenant_id=? AND p.owner_user_id=? AND v.status='delivered' AND mp.id IS NULL
                ORDER BY v.created_at DESC
                """,
                (principal.tenant_id, principal.actor_id),
            ).fetchall()
            billing = connection.execute(
                """
                SELECT COUNT(*) AS orders, COALESCE(SUM(amount_minor),0) AS gross_minor,
                       COALESCE(SUM(publisher_amount_minor),0) AS payable_minor
                FROM marketplace_orders WHERE publisher_user_id=? AND status='paid'
                """,
                (principal.actor_id,),
            ).fetchone()
        return {
            "publications": [self._public_publication(dict(item), private=True) for item in publications],
            "projects": [dict(item) for item in projects],
            "delivered_versions": [self._version_summary(dict(item)) for item in versions],
            "billing": dict(billing),
        }

    def publish(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("creator")
        version_id = self._text(payload, "version_id", 100)
        with self.database.session() as connection:
            version = connection.execute(
                """
                SELECT v.*, p.name AS project_name, p.tenant_id, p.owner_user_id
                FROM skill_versions v JOIN projects p ON p.id=v.project_id
                WHERE v.id=? AND p.tenant_id=?
                """,
                (version_id, principal.tenant_id),
            ).fetchone()
        if version is None or version["owner_user_id"] != principal.actor_id:
            raise AppError("version_not_found", "没有找到可发布的个人 Skill 版本。", 404)
        if version["status"] != "delivered" or not ARTIFACT_DIGEST_RE.fullmatch(str(version["artifact_digest"])):
            raise AppError("publication_gate_denied", "只有已交付且摘要完整的 Skill 版本可以发布。", 409)
        with self.database.session() as connection:
            existing_version = connection.execute(
                "SELECT id FROM marketplace_publications WHERE skill_version_id=?", (version_id,)
            ).fetchone()
        if existing_version:
            raise AppError("version_already_published", "该交付版本已经发布，无需重复发布。", 409, {"publication_id": existing_version["id"]})
        name = str(payload.get("name") or version["project_name"]).strip()
        if not name or len(name) > 100:
            raise AppError("invalid_publication_name", "发布名称必须为 1–100 个字符。")
        slug = str(payload.get("slug") or self._slug(name)).strip().lower()
        if not SLUG_RE.fullmatch(slug):
            raise AppError("invalid_publication_slug", "短链接只能使用小写字母、数字和连字符。")
        summary = self._text(payload, "summary", 600)
        category = str(payload.get("category") or "productivity")
        if category not in CATEGORIES:
            raise AppError("invalid_publication_category", "Skill 分类不正确。")
        pricing_type, price_minor, currency = self._pricing(payload)
        license_id = str(payload.get("license_id") or "unspecified").strip()[:80] or "unspecified"
        compatibility = self._string_list(payload.get("compatibility"), 8, 80)
        permissions = self._string_list(payload.get("permissions"), 12, 120)
        data_policy = str(payload.get("data_policy") or "not_declared")
        if data_policy not in {"not_declared", "local_only", "declared_remote"}:
            raise AppError("invalid_data_policy", "数据处理声明不正确。")
        support_policy = str(payload.get("support_policy") or "community")
        if support_policy not in {"community", "maintained", "enterprise"}:
            raise AppError("invalid_support_policy", "支持政策不正确。")
        publication_id = make_id("pub")
        now = utc_now()
        with self.database.transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO marketplace_publications(
                        id, tenant_id, publisher_user_id, project_id, skill_version_id, name, slug, summary,
                        category, canonicalization_version, digest_algorithm, artifact_digest, pricing_type,
                        price_minor, currency, status, published_at, created_at, updated_at, license_id,
                        compatibility_json, permissions_json, data_policy, support_policy, curation_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed')
                    """,
                    (
                        publication_id, principal.tenant_id, principal.actor_id, version["project_id"], version_id,
                        name, slug, summary, category, version["canonicalization_version"], version["digest_algorithm"],
                        version["artifact_digest"], pricing_type, price_minor, currency, now, now, now,
                        license_id, json_dumps(compatibility), json_dumps(permissions), data_policy, support_policy,
                    ),
                )
            except Exception as exc:
                if "UNIQUE" in str(exc).upper():
                    if "skill_version_id" in str(exc):
                        raise AppError("version_already_published", "该交付版本已经发布，无需重复发布。", 409) from exc
                    raise AppError("publication_slug_exists", "该短链接已被使用。", 409) from exc
                raise
            self.control_plane._audit(
                connection, principal, "marketplace.publication.published", "publication", publication_id,
                {"digest": version["artifact_digest"], "pricing_type": pricing_type, "price_minor": price_minor},
            )
            row = connection.execute("SELECT * FROM marketplace_publications WHERE id=?", (publication_id,)).fetchone()
        return self._public_publication(dict(row), private=True)

    def update_publication(self, principal: Principal, publication_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("creator")
        current = self._owned_publication(principal, publication_id)
        changes: dict[str, Any] = {}
        if any(key in payload for key in ("pricing_type", "price_minor", "currency")):
            pricing, price, currency = self._pricing({
                "pricing_type": payload.get("pricing_type", current["pricing_type"]),
                "price_minor": payload.get("price_minor", current["price_minor"]),
                "currency": payload.get("currency", current["currency"]),
            })
            changes.update({"pricing_type": pricing, "price_minor": price, "currency": currency})
        if "summary" in payload:
            changes["summary"] = self._text(payload, "summary", 600)
        if "status" in payload:
            value = str(payload["status"])
            if value not in {"published", "unlisted"}:
                raise AppError("invalid_publication_status", "个人用户只能发布或下架自己的 Skill。")
            changes["status"] = value
            changes["published_at"] = utc_now() if value == "published" else current["published_at"]
        if not changes:
            return self._public_publication(current, private=True)
        changes["updated_at"] = utc_now()
        with self.database.transaction() as connection:
            columns = list(changes)
            connection.execute(
                f"UPDATE marketplace_publications SET {', '.join(f'{key}=?' for key in columns)} WHERE id=? AND publisher_user_id=?",
                [changes[key] for key in columns] + [publication_id, principal.actor_id],
            )
            self.control_plane._audit(connection, principal, "marketplace.publication.updated", "publication", publication_id, {"fields": columns})
            row = connection.execute("SELECT * FROM marketplace_publications WHERE id=?", (publication_id,)).fetchone()
        return self._public_publication(dict(row), private=True)

    def purchase(self, principal: Principal, publication_id: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        principal.require("buyer")
        if len(idempotency_key) < 12 or len(idempotency_key) > 200:
            raise AppError("idempotency_key_required", "获取 Skill 需要 12–200 字符的 Idempotency-Key。")
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT * FROM marketplace_publications WHERE id=? AND status='published'", (publication_id,)
            ).fetchone()
        if row is None:
            raise AppError("publication_not_found", "Skill 发布内容不存在。", 404)
        publication = dict(row)
        if publication["publisher_user_id"] == principal.actor_id:
            raise AppError("self_purchase_not_allowed", "发布者无需购买自己的 Skill。", 409)
        if publication["pricing_type"] == "paid" and payload.get("accept_sandbox_charge") is not True:
            raise AppError("sandbox_charge_confirmation_required", "请确认这是沙箱计费后再继续。", 409)
        now = utc_now()
        with self.database.transaction() as connection:
            existing_order = connection.execute(
                "SELECT * FROM marketplace_orders WHERE tenant_id=? AND idempotency_key=?",
                (principal.tenant_id, idempotency_key),
            ).fetchone()
            if existing_order:
                library = connection.execute(
                    "SELECT * FROM user_skill_library WHERE order_id=?", (existing_order["id"],)
                ).fetchone()
                return {"library_entry": dict(library) if library else None, "order": dict(existing_order), "already_owned": bool(library)}
            existing_library = connection.execute(
                "SELECT * FROM user_skill_library WHERE user_id=? AND publication_id=?",
                (principal.actor_id, publication_id),
            ).fetchone()
            if existing_library:
                return {"library_entry": dict(existing_library), "order": None, "already_owned": True}
            amount = int(publication["price_minor"])
            fee = math.floor(amount * int(publication["platform_fee_bps"]) / 10_000)
            publisher_amount = amount - fee
            order_id = make_id("ord")
            connection.execute(
                """
                INSERT INTO marketplace_orders(
                    id, tenant_id, buyer_user_id, publication_id, publisher_user_id, idempotency_key,
                    amount_minor, platform_fee_minor, publisher_amount_minor, currency, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id, principal.tenant_id, principal.actor_id, publication_id, publication["publisher_user_id"],
                    idempotency_key, amount, fee, publisher_amount, publication["currency"], now,
                ),
            )
            if amount > 0:
                self._post_order_transaction(connection, principal, order_id, publication, amount, fee, publisher_amount, now)
            library_id = make_id("lib")
            connection.execute(
                "INSERT INTO user_skill_library(id, user_id, publication_id, order_id, artifact_digest, acquired_at) VALUES (?, ?, ?, ?, ?, ?)",
                (library_id, principal.actor_id, publication_id, order_id, publication["artifact_digest"], now),
            )
            connection.execute("UPDATE marketplace_publications SET purchase_count=purchase_count+1, updated_at=? WHERE id=?", (now, publication_id))
            self.control_plane._audit(
                connection, principal, "marketplace.skill.acquired", "publication", publication_id,
                {"order_id": order_id, "amount_minor": amount, "mode": "sandbox"},
            )
            order = connection.execute("SELECT * FROM marketplace_orders WHERE id=?", (order_id,)).fetchone()
            library = connection.execute("SELECT * FROM user_skill_library WHERE id=?", (library_id,)).fetchone()
        return {"library_entry": dict(library), "order": dict(order), "already_owned": False}

    def review(self, principal: Principal, publication_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        principal.require("buyer")
        try:
            rating = int(payload.get("rating"))
        except (TypeError, ValueError) as exc:
            raise AppError("invalid_rating", "评分必须是 1–5 的整数。") from exc
        if rating < 1 or rating > 5:
            raise AppError("invalid_rating", "评分必须是 1–5 的整数。")
        title = str(payload.get("title") or "").strip()[:100]
        body = str(payload.get("body") or "").strip()
        if len(body) > 1200:
            raise AppError("review_too_long", "评价内容不能超过 1,200 个字符。")
        now = utc_now()
        with self.database.transaction() as connection:
            owned = connection.execute(
                "SELECT 1 FROM user_skill_library WHERE user_id=? AND publication_id=?",
                (principal.actor_id, publication_id),
            ).fetchone()
            if not owned:
                raise AppError("review_requires_ownership", "获取并使用该 Skill 后才能评价。", 409)
            review_id = make_id("revw")
            connection.execute(
                """
                INSERT INTO marketplace_reviews(id, publication_id, reviewer_user_id, rating, title, body, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(publication_id, reviewer_user_id) DO UPDATE SET
                    rating=excluded.rating, title=excluded.title, body=excluded.body, status='published', updated_at=excluded.updated_at
                """,
                (review_id, publication_id, principal.actor_id, rating, title, body, now, now),
            )
            stats = connection.execute(
                "SELECT COUNT(*) AS n, COALESCE(AVG(rating),0) AS average FROM marketplace_reviews WHERE publication_id=? AND status='published'",
                (publication_id,),
            ).fetchone()
            connection.execute(
                "UPDATE marketplace_publications SET review_count=?, average_rating=?, updated_at=? WHERE id=?",
                (stats["n"], round(float(stats["average"]), 2), now, publication_id),
            )
            row = connection.execute(
                "SELECT * FROM marketplace_reviews WHERE publication_id=? AND reviewer_user_id=?",
                (publication_id, principal.actor_id),
            ).fetchone()
            self.control_plane._audit(connection, principal, "marketplace.review.saved", "review", row["id"], {"rating": rating})
        return dict(row)

    def library(self, principal: Principal) -> list[dict[str, Any]]:
        with self.database.session() as connection:
            rows = connection.execute(
                """
                SELECT l.*, p.name, p.summary, p.category, p.pricing_type, p.price_minor, p.currency,
                       u.display_name AS publisher_name
                FROM user_skill_library l JOIN marketplace_publications p ON p.id=l.publication_id
                JOIN users u ON u.id=p.publisher_user_id WHERE l.user_id=? ORDER BY l.acquired_at DESC
                """,
                (principal.actor_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _owned_publication(self, principal: Principal, publication_id: str) -> dict[str, Any]:
        with self.database.session() as connection:
            row = connection.execute(
                "SELECT * FROM marketplace_publications WHERE id=? AND tenant_id=? AND publisher_user_id=?",
                (publication_id, principal.tenant_id, principal.actor_id),
            ).fetchone()
        if row is None:
            raise AppError("publication_not_found", "没有找到该发布内容。", 404)
        return dict(row)

    @staticmethod
    def _post_order_transaction(
        connection: Any, principal: Principal, order_id: str, publication: dict[str, Any],
        amount: int, fee: int, publisher_amount: int, now: str,
    ) -> None:
        transaction_id = make_id("txn")
        connection.execute(
            "INSERT INTO journal_transactions(id, tenant_id, source_type, source_id, currency, description, created_at) VALUES (?, ?, 'marketplace_order', ?, ?, 'Sandbox Skill purchase', ?)",
            (transaction_id, publication["tenant_id"], order_id, publication["currency"], now),
        )
        lines = [
            (make_id("line"), transaction_id, "sandbox_buyer_charge", "debit", amount, principal.actor_id, now),
            (make_id("line"), transaction_id, "publisher_payable", "credit", publisher_amount, publication["publisher_user_id"], now),
        ]
        if fee:
            lines.append((make_id("line"), transaction_id, "platform_revenue", "credit", fee, "skillsentra", now))
        connection.executemany(
            "INSERT INTO journal_lines(id, transaction_id, account_code, side, amount_minor, party_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            lines,
        )

    @staticmethod
    def _public_publication(record: dict[str, Any], private: bool = False) -> dict[str, Any]:
        allowed = {
            "id", "publisher_user_id", "publisher_name", "name", "slug", "summary", "category",
            "canonicalization_version", "digest_algorithm", "artifact_digest", "pricing_type", "price_minor",
            "currency", "status", "average_rating", "review_count", "purchase_count", "published_at",
            "created_at", "updated_at", "rank_score", "license_id", "data_policy", "support_policy",
            "curation_status",
        }
        if private:
            allowed.update({"tenant_id", "project_id", "skill_version_id", "snapshot_id", "platform_fee_bps"})
        public = {key: value for key, value in record.items() if key in allowed}
        public["compatibility"] = MarketplaceService._decode_list(record.get("compatibility_json"))
        public["permissions"] = MarketplaceService._decode_list(record.get("permissions_json"))
        return public

    @staticmethod
    def _trust_passport(record: dict[str, Any], lifecycle: dict[str, Any], validations: list[dict[str, Any]]) -> dict[str, Any]:
        latest: dict[str, dict[str, Any]] = {}
        for item in validations:
            latest.setdefault(str(item.get("stage") or "unknown"), item)
        stored: dict[str, Any] = {}
        try:
            stored = json.loads(str(record.get("trust_evidence_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            stored = {}
        if stored and "static" not in latest:
            latest["static"] = stored
        route = str(lifecycle.get("route") or "")
        required = ("static", "evaluation", "regression") if route == "template" else (("static", "baseline", "regression") if route == "existing" else ("static",))
        gate_passed = all(latest.get(stage, {}).get("status") == "passed" for stage in required)
        status = "verified_delivery" if lifecycle.get("delivery_status") == "delivered" and gate_passed else ("static_verified" if latest.get("static", {}).get("status") == "passed" else "declared")
        evidence = []
        for stage, item in latest.items():
            evidence.append({
                "stage": stage,
                "status": item.get("status", "unknown"),
                "score": item.get("score", 0),
                "created_at": item.get("created_at"),
                "finding_count": len(MarketplaceService._decode_json_list(item.get("findings_json") or item.get("findings"))),
            })
        evidence.sort(key=lambda item: item["stage"])
        return {
            "status": status,
            "curation_status": record.get("curation_status", "unreviewed"),
            "identity": {
                "canonicalization_version": record.get("canonicalization_version"),
                "digest_algorithm": record.get("digest_algorithm"),
                "artifact_digest": record.get("artifact_digest"),
                "version": lifecycle.get("skill_version"),
            },
            "delivery_status": lifecycle.get("delivery_status") or "catalog_snapshot",
            "gate_status": "passed" if gate_passed else "partial",
            "evidence": evidence,
            "dynamic_safety": "unknown",
            "license_id": record.get("license_id") or "unspecified",
            "compatibility": MarketplaceService._decode_list(record.get("compatibility_json")),
            "permissions": MarketplaceService._decode_list(record.get("permissions_json")),
            "data_policy": record.get("data_policy") or "not_declared",
            "support_policy": record.get("support_policy") or "community",
            "update_status": lifecycle.get("update_status") or ("monitored" if lifecycle.get("updates_enabled") else "not_configured"),
            "last_verified_at": max((str(item.get("created_at") or "") for item in latest.values()), default="") or record.get("updated_at"),
        }

    @staticmethod
    def _decode_json_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(str(value or "[]"))
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    @staticmethod
    def _decode_list(value: Any) -> list[str]:
        return [str(item) for item in MarketplaceService._decode_json_list(value) if str(item).strip()]

    @staticmethod
    def _string_list(value: Any, maximum_items: int, maximum_length: int) -> list[str]:
        if isinstance(value, str):
            items = [item.strip() for item in re.split(r"[,，\n]", value) if item.strip()]
        elif isinstance(value, list):
            items = [str(item).strip() for item in value if str(item).strip()]
        else:
            items = []
        if len(items) > maximum_items or any(len(item) > maximum_length for item in items):
            raise AppError("invalid_trust_metadata", "信任护照字段数量或长度超过限制。")
        return items

    @staticmethod
    def _version_summary(record: dict[str, Any]) -> dict[str, Any]:
        return {key: record.get(key) for key in (
            "id", "project_id", "version", "artifact_digest", "canonicalization_version", "digest_algorithm", "status", "created_at"
        )}

    @staticmethod
    def _pricing(payload: dict[str, Any]) -> tuple[str, int, str]:
        pricing_type = str(payload.get("pricing_type") or "free")
        if pricing_type not in {"free", "paid"}:
            raise AppError("invalid_pricing_type", "请选择免费或收费。")
        try:
            price = int(payload.get("price_minor") or 0)
        except (TypeError, ValueError) as exc:
            raise AppError("invalid_price", "价格必须使用整数最小货币单位。") from exc
        currency = str(payload.get("currency") or "USD").upper()
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise AppError("invalid_currency", "币种必须是三位大写代码。")
        if pricing_type == "free":
            price = 0
        elif price < 50 or price > 100_000_000:
            raise AppError("invalid_price", "收费 Skill 的价格必须在 50–100,000,000 最小货币单位之间。")
        return pricing_type, price, currency

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return (slug or f"skill-{make_id('pub')[-6:]}")[:60].strip("-")

    @staticmethod
    def _text(payload: dict[str, Any], field: str, maximum: int) -> str:
        value = str(payload.get(field) or "").strip()
        if not value or len(value) > maximum:
            raise AppError(f"invalid_{field}", f"{field} 必须为 1–{maximum} 个字符。")
        return value
