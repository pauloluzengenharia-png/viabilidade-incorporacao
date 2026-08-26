"""
O backup que não depende de provedor nenhum.

O banco deste sistema mora em um serviço de nuvem no plano gratuito — e plano
gratuito tem letra miúda: expira, pausa, muda de regra. A defesa não é confiar
mais no provedor; é o dado ter sempre uma cópia que ninguém além da MMI
controla. É isso que este módulo faz: **tudo que está no banco vira um arquivo
JSON, e um arquivo JSON vira o banco de volta** — inclusive em outro provedor,
que é como se migra sem a senha do banco passar por pessoa nenhuma.

Duas decisões de projeto:

**O esquema não viaja no arquivo.** Quem cria tabela é a migration, que roda
sozinha quando o serviço sobe. O backup carrega só os dados; restaurar em um
banco de outra versão do sistema é erro na cara, não corrupção silenciosa.

**A ordem das tabelas é descoberta, não decorada.** As chaves estrangeiras
saem do catálogo do Postgres e viram uma ordenação topológica: pais antes de
filhos na restauração, e uma tabela nova entra no backup no dia em que a
migration dela existir, sem ninguém lembrar de atualizar lista nenhuma.
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import q

ESQUEMA = "viab"

#: A versão do formato. Muda quando a estrutura do arquivo mudar — e aí a
#: restauração de um arquivo antigo avisa em vez de adivinhar.
FORMATO = 1


class ArquivoDeBackupInvalido(ValueError):
    """O arquivo não é um backup deste sistema, ou é de outra versão dele."""


# =====================================================================
# o catálogo: tabelas, colunas e a ordem que as FKs impõem
# =====================================================================
def _tabelas(s: Session) -> list[str]:
    return [l["table_name"] for l in q(s, """
        SELECT table_name FROM information_schema.tables
         WHERE table_schema = :e AND table_type = 'BASE TABLE'
         ORDER BY table_name""", e=ESQUEMA)]


def _dependencias(s: Session) -> dict[str, set]:
    """{tabela: {tabelas de que ela depende}} — auto-referências fora."""
    linhas = q(s, """
        SELECT tc.table_name AS filho, ccu.table_name AS pai
          FROM information_schema.table_constraints tc
          JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
           AND ccu.table_schema = tc.table_schema
         WHERE tc.table_schema = :e AND tc.constraint_type = 'FOREIGN KEY'""",
        e=ESQUEMA)
    dep: dict[str, set] = {}
    for l in linhas:
        if l["filho"] != l["pai"]:
            dep.setdefault(l["filho"], set()).add(l["pai"])
    return dep


def ordem_de_carga(s: Session) -> list[str]:
    """As tabelas na ordem em que podem ser inseridas: pais antes de filhos."""
    todas = _tabelas(s)
    dep = _dependencias(s)
    pend = {t: set(dep.get(t, ())) & set(todas) for t in todas}
    ordem: list[str] = []
    while pend:
        prontas = sorted(t for t, p in pend.items() if not p)
        if not prontas:
            # ciclo de FK não existe neste esquema; se um dia existir, melhor
            # quebrar aqui do que restaurar em ordem errada em silêncio
            raise RuntimeError(f"ciclo de chaves estrangeiras entre: {sorted(pend)}")
        for t in prontas:
            ordem.append(t)
            del pend[t]
            for p in pend.values():
                p.discard(t)
    return ordem


# =====================================================================
# exportar
# =====================================================================
def _valor_para_json(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)                      # centavos exatos, sem float no meio
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        raise TypeError("coluna binária no backup — o formato precisa ser revisto")
    return v


def exportar(s: Session) -> dict:
    """O banco inteiro, como um dicionário pronto para virar JSON."""
    tabelas = {}
    for t in ordem_de_carga(s):
        linhas = q(s, f'SELECT * FROM "{t}"')                 # noqa: S608
        tabelas[t] = [{k: _valor_para_json(v) for k, v in l.items()}
                      for l in linhas]
    return {
        "sistema": "viabilidade-mmi",
        "formato": FORMATO,
        "gerado_em": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tabelas": tabelas,
    }


# =====================================================================
# restaurar
# =====================================================================
def _tipos_das_colunas(s: Session, tabela: str) -> dict[str, str]:
    """Colunas graváveis e seus tipos. Coluna gerada fica de fora: o banco a
    recalcula sozinho, e tentar inseri-la é erro — `valor_liquido` do contrato
    é `valor_bruto - comissao` por definição da própria tabela."""
    return {l["column_name"]: l["data_type"] for l in q(s, """
        SELECT column_name, data_type FROM information_schema.columns
         WHERE table_schema = :e AND table_name = :t
           AND is_generated = 'NEVER'""", e=ESQUEMA, t=tabela)}


def _valor_do_json(bruto: Any, tipo: str) -> Any:
    if bruto is None:
        return None
    if tipo == "date":
        return dt.date.fromisoformat(bruto)
    if tipo.startswith("timestamp"):
        return dt.datetime.fromisoformat(bruto)
    if tipo == "numeric":
        return Decimal(bruto)
    if tipo in ("json", "jsonb"):
        # o driver não converte dict sozinho; como texto, o Postgres faz o cast
        return json.dumps(bruto, ensure_ascii=False)
    return bruto


def restaurar(s: Session, dados: dict) -> dict[str, int]:
    """
    Substitui o conteúdo do banco pelo do arquivo. Devolve {tabela: linhas}.

    É tudo-ou-nada dentro da transação de quem chamou: qualquer linha que o
    banco recuse desfaz a restauração inteira. Restauração pela metade é o
    único resultado pior do que nenhuma.
    """
    if not isinstance(dados, dict) or dados.get("sistema") != "viabilidade-mmi":
        raise ArquivoDeBackupInvalido(
            "este arquivo não é um backup do sistema de viabilidade.")
    if dados.get("formato") != FORMATO:
        raise ArquivoDeBackupInvalido(
            f"o arquivo está no formato {dados.get('formato')} e este sistema "
            f"lê o formato {FORMATO}. Gere um backup novo na versão de origem, "
            f"ou atualize os dois lados para a mesma versão.")

    ordem = ordem_de_carga(s)
    vindas = dados.get("tabelas") or {}
    desconhecidas = sorted(set(vindas) - set(ordem))
    if desconhecidas:
        raise ArquivoDeBackupInvalido(
            f"o arquivo traz tabelas que este banco não tem: {desconhecidas}. "
            f"O backup é de uma versão mais nova do sistema — atualize este "
            f"serviço antes de restaurar.")

    # limpa tudo de uma vez; o CASCADE resolve as pontas e o TRUNCATE zera as
    # tabelas semeadas por migration (setor_custo, area_pdp), que o arquivo
    # traz por inteiro
    s.execute(text("SET search_path = viab, public"))
    lista = ", ".join(f'"{t}"' for t in ordem)
    s.execute(text(f"TRUNCATE {lista} CASCADE"))              # noqa: S608

    gravadas: dict[str, int] = {}
    for t in ordem:
        linhas = vindas.get(t) or []
        if not linhas:
            continue
        tipos = _tipos_das_colunas(s, t)
        colunas = [c for c in linhas[0] if c in tipos]
        nomes = ", ".join(f'"{c}"' for c in colunas)
        marcas = ", ".join(f":{c}" for c in colunas)
        sql = text(f'INSERT INTO "{t}" ({nomes}) VALUES ({marcas})')  # noqa: S608
        s.execute(sql, [
            {c: _valor_do_json(l.get(c), tipos[c]) for c in colunas}
            for l in linhas])
        gravadas[t] = len(linhas)

    # as sequências continuam de onde o dado parou — sem isto, o primeiro
    # INSERT depois da restauração colide com um id que já existe
    for l in q(s, """
        SELECT c.table_name AS t, c.column_name AS col,
               pg_get_serial_sequence(:e || '.' || c.table_name, c.column_name) AS seq
          FROM information_schema.columns c
         WHERE c.table_schema = :e AND c.column_default LIKE 'nextval%'""", e=ESQUEMA):
        if l["seq"]:
            s.execute(text(
                f"SELECT setval('{l['seq']}', "                # noqa: S608
                f"COALESCE((SELECT max(\"{l['col']}\") FROM \"{l['t']}\"), 0) + 1, "
                f"false)"))
    return gravadas


def resumo(dados: dict) -> str:
    """Uma linha humana sobre o que o arquivo contém."""
    tabelas = dados.get("tabelas") or {}
    n = sum(len(v) for v in tabelas.values())
    emp = [l.get("nome") for l in tabelas.get("empreendimento", [])]
    quando = (dados.get("gerado_em") or "")[:16].replace("T", " ")
    return (f"{n:,} linhas em {sum(1 for v in tabelas.values() if v)} tabelas"
            .replace(",", " ")
            + (f" · empreendimentos: {', '.join(emp)}" if emp else "")
            + (f" · gerado em {quando} UTC" if quando else ""))
