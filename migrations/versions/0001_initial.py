"""Initial workflow persistence. PostgreSQL in application; SQLite in unit tests."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    # Freeze table definitions in this migration, independently of later ORM changes.
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("tenant_id", sa.String(36)),
        sa.Column("company_name", sa.String(100)),
    )
    op.create_table(
        "login_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
    )
    op.create_table(
        "scenario_versions",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("is_fixture", sa.Boolean, nullable=False),
        sa.Column("config", sa.JSON, nullable=False),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column(
            "scenario_id",
            sa.String(100),
            sa.ForeignKey("scenario_versions.id"),
            nullable=False,
        ),
        sa.Column("config_snapshot", sa.JSON, nullable=False),
        sa.Column("competencies", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.UniqueConstraint("tenant_id", "request_id"),
    )
    op.create_table(
        "invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("alias", sa.String(80), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.Float, nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.UniqueConstraint("project_id", "email"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "invitation_id",
            sa.String(36),
            sa.ForeignKey("invitations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("consent_version", sa.String(20)),
        sa.Column("consent_at", sa.Float),
        sa.Column("accumulated", sa.Float, nullable=False),
        sa.Column("resumed_at", sa.Float),
        sa.Column("heartbeat_at", sa.Float),
        sa.Column("connection_id", sa.String(36)),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("workspace", sa.JSON, nullable=False),
        sa.Column("snapshot", sa.JSON),
        sa.Column("snapshot_id", sa.String(36)),
    )
    op.create_table(
        "session_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("command_id", sa.String(150)),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("elapsed", sa.Float, nullable=False),
        sa.Column("created_at", sa.Float, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.UniqueConstraint("session_id", "seq"),
        sa.UniqueConstraint("session_id", "command_id"),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("sessions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("sent", sa.Boolean, nullable=False),
    )
    op.create_table(
        "reports",
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id"), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
    )
    op.create_table(
        "document_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("approval_id", sa.String(36)),
        sa.Column("decided_by", sa.String(36), sa.ForeignKey("users.id")),
    )
    for table, column in [
        ("users", "tenant_id"),
        ("login_sessions", "user_id"),
        ("projects", "tenant_id"),
        ("invitations", "project_id"),
        ("invitations", "email"),
        ("sessions", "project_id"),
        ("sessions", "user_id"),
        ("session_events", "session_id"),
        ("document_imports", "tenant_id"),
    ]:
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in [
        "document_imports",
        "reports",
        "outbox",
        "session_events",
        "sessions",
        "invitations",
        "projects",
        "scenario_versions",
        "login_sessions",
        "users",
    ]:
        op.drop_table(table)
