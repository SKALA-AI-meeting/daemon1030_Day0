import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "rag_documents",
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
    )
    op.execute(
        "CREATE TABLE rag_chunks (id varchar(150) PRIMARY KEY, document_id varchar(100) NOT NULL REFERENCES rag_documents(id), tenant_id varchar(36) NOT NULL, content text NOT NULL, model_id varchar(200) NOT NULL, embedding vector NOT NULL)"
    )
    op.create_index("ix_rag_chunks_scope", "rag_chunks", ["tenant_id", "document_id"])


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_table("rag_chunks")
        op.drop_table("rag_documents")
