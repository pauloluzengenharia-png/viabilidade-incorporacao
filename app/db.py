"""Conexão com o Postgres e execução das migrations."""
from __future__ import annotations

import os
import pathlib

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

RAIZ = pathlib.Path(__file__).resolve().parent.parent


def url_do_banco() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL não definida. No Railway ela vem de ${{Postgres.DATABASE_URL}}; "
            "localmente, exporte a URL de um Postgres seu."
        )
    # o Railway entrega postgres://, o SQLAlchemy 2 quer postgresql+psycopg2://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


engine = create_engine(url_do_banco(), pool_pre_ping=True, future=True)
Sessao = sessionmaker(bind=engine, future=True, expire_on_commit=False)


def sessao() -> Session:
    """Dependência do FastAPI."""
    s = Sessao()
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------
# migrations: arquivos .sql numerados, aplicados uma vez cada
# ---------------------------------------------------------------------
def aplicar_migrations() -> list[str]:
    aplicadas: list[str] = []
    with engine.begin() as con:
        # sempre qualificada: o `SET search_path = viab` das migrations gruda na
        # conexão do pool, e sem o `public.` a tabela de controle acaba criada
        # dentro do próprio schema que ela existe para proteger — e some junto
        # num DROP SCHEMA CASCADE.
        con.execute(text("""
            CREATE TABLE IF NOT EXISTS public.migration_aplicada (
                arquivo     text PRIMARY KEY,
                aplicada_em timestamptz NOT NULL DEFAULT now()
            )"""))
        ja = {r[0] for r in
              con.execute(text("SELECT arquivo FROM public.migration_aplicada"))}

    for caminho in sorted((RAIZ / "migrations").glob("*.sql")):
        if caminho.name in ja:
            continue
        sql = caminho.read_text(encoding="utf-8")
        # tudo pelo cursor cru: o SQL das migrations tem $$ e vários statements,
        # que o SQLAlchemy não sabe parametrizar. Misturar os dois caminhos na
        # mesma transação faz o INSERT de controle sumir no rollback.
        with engine.begin() as con:
            cur = con.connection.cursor()
            cur.execute("SET search_path = viab, public")
            cur.execute(sql)
            cur.execute(
                "INSERT INTO public.migration_aplicada (arquivo) VALUES (%s)",
                (caminho.name,))
        aplicadas.append(caminho.name)
    return aplicadas


def q(sessao_: Session, sql: str, /, **p):
    """
    Atalho: executa SQL com o search_path certo e devolve as linhas como dicts.

    Os dois primeiros parâmetros são posicionais-apenas (a barra) para que
    nomes curtos como `s`, `e` ou `c` possam ser usados como parâmetro de bind
    sem colidir com a assinatura.
    """
    sessao_.execute(text("SET search_path = viab, public"))
    res = sessao_.execute(text(sql), p)
    if res.returns_rows:
        return [dict(r._mapping) for r in res]
    return []
