"""
O cronograma dentro do estudo: gravar os marcos e explicar o que eles mudam.

Este módulo é a ponte entre `pdp.py`, que sabe ler o PDP, e o resto do sistema,
que sabe calcular. Ele grava, conta o que gravou, e responde à pergunta que o
usuário faz na tela: *o que muda no meu caixa por causa disso?*

Uma decisão de projeto que vale explicitar: **sincronizar substitui**. Não há
merge, não há "manter o que foi editado aqui". Quem manda no cronograma é o
PDP; se o estudo pudesse divergir, em duas semanas ninguém saberia qual das
duas datas é a verdadeira.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from .db import q
from .pdp import PDP, MarcoPDP, PDPIndisponivel


# =====================================================================
# gravação
# =====================================================================
def gravar_marcos(s: Session, emp_id: int, marcos: list[MarcoPDP]) -> tuple[int, int]:
    """
    Substitui o cronograma do empreendimento. Devolve (marcos, dependências).

    Os itens de composição amarrados a marcos são preservados pelo `pdp_id`:
    apagar e recriar a linha do marco perderia a amarração, então a gravação é
    um upsert pela chave (empreendimento, pdp_id), e só some o marco que
    realmente deixou de existir no PDP.
    """
    vistos = []
    for m in marcos:
        r = q(s, """
            INSERT INTO marco (empreendimento_id, pdp_id, nome, area_codigo,
                               processo, fase, inicio, fim, duracao, progresso,
                               critico, sincronizado_em)
            VALUES (:e, :p, :n, :a, :pr, :f, :i, :fim, :d, :g, :c, now())
            ON CONFLICT (empreendimento_id, pdp_id) DO UPDATE SET
              nome = EXCLUDED.nome, area_codigo = EXCLUDED.area_codigo,
              processo = EXCLUDED.processo, fase = EXCLUDED.fase,
              inicio = EXCLUDED.inicio, fim = EXCLUDED.fim,
              duracao = EXCLUDED.duracao, progresso = EXCLUDED.progresso,
              critico = EXCLUDED.critico, sincronizado_em = now()
            RETURNING id
        """, e=emp_id, p=m.pdp_id, n=m.nome, a=m.area_codigo, pr=m.processo,
             f=m.fase, i=m.inicio, fim=m.fim, d=m.duracao, g=m.progresso,
             c=m.critico)[0]["id"]
        vistos.append(m.pdp_id)
        q(s, "DELETE FROM marco_dependencia WHERE marco_id = :m", m=r)
        for pred, tipo, lag in m.predecessores:
            q(s, """INSERT INTO marco_dependencia (marco_id, predecessor, tipo, defasagem)
                    VALUES (:m, :p, :t, :l) ON CONFLICT DO NOTHING""",
              m=r, p=pred, t=tipo, l=lag)

    if vistos:
        q(s, """DELETE FROM marco WHERE empreendimento_id = :e
                  AND pdp_id <> ALL(:v)""", e=emp_id, v=vistos)

    n_dep = q(s, """SELECT count(*) AS n FROM marco_dependencia d
                      JOIN marco m ON m.id = d.marco_id
                     WHERE m.empreendimento_id = :e""", e=emp_id)[0]["n"]
    return len(vistos), int(n_dep)


def sincronizar(s: Session, emp_id: int, autor: str) -> dict:
    """
    Lê o PDP e grava. Nunca levanta: devolve o resultado, bom ou ruim.

    Quem chama é uma tela, e uma tela precisa contar o que aconteceu — inclusive
    quando o que aconteceu foi o PDP estar fora do ar.
    """
    emp = q(s, "SELECT id, nome, pdp_project_id FROM empreendimento WHERE id = :e",
            e=emp_id)[0]
    if not emp["pdp_project_id"]:
        msg = ("este empreendimento ainda não tem o código do projeto no PDP. "
               "Preencha-o no cadastro — é o número que aparece em project_id "
               "na URL do PDP.")
        _registrar(s, emp_id, autor, False, 0, 0, msg)
        return {"ok": False, "mensagem": msg}

    try:
        with PDP() as pdp:
            marcos = pdp.marcos(int(emp["pdp_project_id"]))
        n, d = gravar_marcos(s, emp_id, marcos)
    except PDPIndisponivel as e:
        _registrar(s, emp_id, autor, False, 0, 0, str(e))
        return {"ok": False, "mensagem": str(e)}
    except Exception as e:                                    # noqa: BLE001
        msg = f"a leitura do PDP falhou de um jeito inesperado: {e}"
        _registrar(s, emp_id, autor, False, 0, 0, msg)
        return {"ok": False, "mensagem": msg}

    sem_area = sum(1 for m in marcos if not m.area_codigo)
    msg = f"{n} marcos e {d} ligações de precedência."
    if sem_area:
        msg += (f" {sem_area} marco(s) vieram sem área e por isso não entram em "
                f"setor nenhum — confira a classificação deles no PDP.")
    _registrar(s, emp_id, autor, True, n, d, msg)
    return {"ok": True, "mensagem": msg, "marcos": n, "dependencias": d}


def _registrar(s: Session, emp_id: int, autor: str, ok: bool,
               n: int, d: int, msg: str) -> None:
    q(s, """INSERT INTO sincronizacao_pdp
              (empreendimento_id, autor, ok, marcos, dependencias, mensagem)
            VALUES (:e, :a, :o, :n, :d, :m)""",
      e=emp_id, a=autor, o=ok, n=n, d=d, m=msg)


def ultima_sincronizacao(s: Session, emp_id: int) -> Optional[dict]:
    r = q(s, """SELECT * FROM sincronizacao_pdp WHERE empreendimento_id = :e
                 ORDER BY quando DESC LIMIT 1""", e=emp_id)
    return r[0] if r else None


# =====================================================================
# leitura para as telas
# =====================================================================
def por_area(s: Session, emp_id: int) -> list[dict]:
    """As áreas do PDP com os marcos deste empreendimento e o setor que pagam."""
    areas = q(s, """SELECT a.codigo, a.nome, a.setor, a.papel, a.ordem,
                           sc.nome AS setor_nome, sc.linha_dre
                      FROM area_pdp a
                      LEFT JOIN setor_custo sc ON sc.codigo = a.setor
                     ORDER BY a.ordem, a.nome""")
    marcos = q(s, """SELECT * FROM marco WHERE empreendimento_id = :e
                      ORDER BY fim, inicio, pdp_id""", e=emp_id)
    por = {}
    for m in marcos:
        por.setdefault(m["area_codigo"], []).append(m)

    saida = []
    for a in areas:
        ms = por.get(a["codigo"], [])
        if not ms:
            continue
        datas_i = [m["inicio"] for m in ms if m["inicio"]]
        datas_f = [m["fim"] for m in ms if m["fim"]]
        saida.append({**a, "marcos": ms, "n": len(ms),
                      "criticos": sum(1 for m in ms if m["critico"]),
                      "inicio": min(datas_i) if datas_i else None,
                      "fim": max(datas_f) if datas_f else None})
    return saida


def marcos_para_escolha(s: Session, emp_id: int, setor: str) -> list[dict]:
    """
    Os marcos que fazem sentido oferecer num item de composição deste setor.

    Primeiro os das áreas que alimentam o setor — são os candidatos naturais —
    e depois todos os outros, porque uma taxa pode muito bem estar amarrada a
    um marco de outra área (o alvará que a Engenharia espera, por exemplo).
    """
    return q(s, """
        SELECT m.id, m.pdp_id, m.nome, m.fim, m.critico,
               a.nome AS area_nome,
               (a.setor = :s) AS do_setor
          FROM marco m
          LEFT JOIN area_pdp a ON a.codigo = m.area_codigo
         WHERE m.empreendimento_id = :e
         ORDER BY (a.setor = :s) DESC NULLS LAST, m.fim, m.pdp_id
    """, e=emp_id, s=setor)


def resumo_do_setor(s: Session, emp_id: int, cenario_id: int, setor: str) -> dict:
    """Janela do setor no cronograma e quantos itens já têm data própria."""
    j = q(s, """SELECT min(m.inicio) AS inicio, max(m.fim) AS fim, count(*) AS marcos
                  FROM marco m JOIN area_pdp a ON a.codigo = m.area_codigo
                 WHERE m.empreendimento_id = :e AND a.setor = :s""",
          e=emp_id, s=setor)[0]
    i = q(s, """SELECT count(*) AS total, count(marco_id) AS com_marco
                  FROM composicao_item WHERE cenario_id = :c AND setor = :s""",
          c=cenario_id, s=setor)[0]
    return {**dict(j), **dict(i)}
