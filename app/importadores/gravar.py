"""Gravação dos registros normalizados no banco, de forma idempotente."""
from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from ..db import q
from .sienge import fim_do_mes


def _registrar(s: Session, emp_id: int, fonte: str, origem: str,
               arquivo: Optional[str], lidas: int, gravadas: int,
               avisos: list[str]) -> int:
    import json
    return q(s, """
        INSERT INTO importacao (empreendimento_id, fonte, origem, arquivo,
                                linhas_lidas, linhas_gravadas, avisos)
        VALUES (:e, :f, :o, :a, :l, :g, CAST(:av AS jsonb)) RETURNING id
    """, e=emp_id, f=fonte, o=origem, a=arquivo, l=lidas, g=gravadas,
        av=json.dumps(avisos, ensure_ascii=False))[0]["id"]


def _mapa_unidades(s: Session, emp_id: int) -> dict[str, int]:
    return {l["nome"]: l["id"] for l in
            q(s, "SELECT id, nome FROM unidade WHERE empreendimento_id = :e", e=emp_id)}


# ---------------------------------------------------------------------
def gravar_unidades(s: Session, emp_id: int, linhas: list[dict],
                    origem="upload", arquivo=None) -> dict:
    gravadas = 0
    for u in linhas:
        q(s, """
            INSERT INTO unidade (empreendimento_id, sienge_unit_id, nome,
                                 tipo_imovel, area_privativa, fracao_ideal, situacao)
            VALUES (:e, :sid, :n, :t, :a, :f, CAST(:s AS situacao_unidade))
            ON CONFLICT (empreendimento_id, nome) DO UPDATE
               SET sienge_unit_id = EXCLUDED.sienge_unit_id,
                   tipo_imovel    = EXCLUDED.tipo_imovel,
                   area_privativa = EXCLUDED.area_privativa,
                   fracao_ideal   = EXCLUDED.fracao_ideal,
                   situacao       = EXCLUDED.situacao
        """, e=emp_id, sid=u["sienge_unit_id"], n=u["nome"], t=u["tipo_imovel"],
            a=u["area_privativa"], f=u["fracao_ideal"], s=u["situacao"])
        gravadas += 1
    _registrar(s, emp_id, "unidades", origem, arquivo, len(linhas), gravadas, [])
    s.commit()
    return {"lidas": len(linhas), "gravadas": gravadas}


def gravar_contratos(s: Session, emp_id: int, linhas: list[dict],
                     origem="upload", arquivo=None) -> dict:
    """
    O Sienge não guarda a comissão do corretor: `Column1.value` e
    `Column1.totalSellingValue` vêm iguais, ambos já líquidos. A comissão é
    convenção comercial da casa — está embutida no preço de tabela, que é o
    valor do contrato dividido por (1 − %comissão).

    Na planilha isso aparece como `Comercial!E6 = 531300 − F6`, com o preço de
    tabela digitado à mão em cada linha. Aqui a conta é explícita e vem da
    tabela de venda cadastrada.
    """
    mapa = _mapa_unidades(s, emp_id)
    perc = q(s, """SELECT perc_comissao FROM tabela_venda
                    WHERE empreendimento_id = :e AND nome = 'Padrão'""", e=emp_id)
    perc_comissao = float(perc[0]["perc_comissao"]) if perc else 0.0
    permutadas = {l["nome"] for l in q(s, """
        SELECT nome FROM unidade
         WHERE empreendimento_id = :e AND situacao = 'Permuta'""", e=emp_id)}

    gravadas, avisos = 0, []
    for c in linhas:
        uid = mapa.get(c["unidade"])
        if not uid:
            avisos.append(f"contrato de unidade desconhecida: {c['unidade']}")
            continue
        liquido = c["valor_total"] or c["valor_venda"] or 0.0
        # permuta não paga comissão de corretagem
        taxa = 0.0 if c["unidade"] in permutadas else perc_comissao
        bruto = liquido / (1 - taxa) if taxa < 1 else liquido
        q(s, """
            INSERT INTO contrato (unidade_id, sienge_contract_id, sienge_bill_id,
                cliente_nome, data_contrato, valor_bruto, comissao, indexador, situacao)
            VALUES (:u, :cid, :bid, :cli, :d, :vb, :com, :idx, :sit)
            ON CONFLICT (sienge_contract_id) DO UPDATE
               SET cliente_nome = EXCLUDED.cliente_nome,
                   data_contrato = EXCLUDED.data_contrato,
                   valor_bruto  = EXCLUDED.valor_bruto,
                   comissao     = EXCLUDED.comissao,
                   situacao     = EXCLUDED.situacao
        """, u=uid, cid=c["sienge_contract_id"], bid=c["sienge_bill_id"],
            cli=c["cliente_nome"], d=c["data_contrato"], vb=bruto,
            com=bruto - liquido, idx=c["indexador"], sit=c["situacao"])
        gravadas += 1
    _registrar(s, emp_id, "contratos", origem, arquivo, len(linhas), gravadas, avisos)
    s.commit()
    return {"lidas": len(linhas), "gravadas": gravadas, "avisos": avisos}


def gravar_receber(s: Session, emp_id: int, linhas: list[dict],
                   origem="upload", arquivo=None, substituir=True) -> dict:
    mapa = _mapa_unidades(s, emp_id)
    if substituir:
        q(s, """DELETE FROM parcela_receber p USING unidade u
                 WHERE u.id = p.unidade_id AND u.empreendimento_id = :e""", e=emp_id)
    gravadas, avisos = 0, []
    for p in linhas:
        uid = mapa.get(p["unidade"])
        if not uid:
            avisos.append(f"parcela de unidade desconhecida: {p['unidade']}")
            continue
        q(s, """
            INSERT INTO parcela_receber (unidade_id, sienge_bill_id,
                sienge_installment_id, numero_parcela, condicao, vencimento,
                valor, indexador)
            VALUES (:u, :b, :i, :n, :c, :v, :val, :idx)
        """, u=uid, b=p["sienge_bill_id"], i=p["sienge_installment_id"],
            n=p["numero_parcela"], c=p["condicao"], v=p["vencimento"],
            val=p["valor"], idx=p["indexador"])
        gravadas += 1
    _registrar(s, emp_id, "receber", origem, arquivo, len(linhas), gravadas,
               avisos[:50])
    s.commit()
    return {"lidas": len(linhas), "gravadas": gravadas, "avisos": avisos[:50]}


def gravar_recebido(s: Session, emp_id: int, linhas: list[dict],
                    origem="upload", arquivo=None, substituir=True) -> dict:
    mapa = _mapa_unidades(s, emp_id)
    if substituir:
        q(s, """DELETE FROM parcela_recebida p USING unidade u
                 WHERE u.id = p.unidade_id AND u.empreendimento_id = :e""", e=emp_id)
    gravadas, avisos = 0, []
    for p in linhas:
        uid = mapa.get(p["unidade"])
        if not uid:
            avisos.append(f"recebimento de unidade desconhecida: {p['unidade']}")
            continue
        q(s, """
            INSERT INTO parcela_recebida (unidade_id, sienge_bill_id,
                sienge_installment_id, data_recebimento, valor_liquido, empresa_id)
            VALUES (:u, :b, :i, :d, :v, :emp)
        """, u=uid, b=p["sienge_bill_id"], i=p["sienge_installment_id"],
            d=p["data_recebimento"], v=p["valor_liquido"], emp=p["empresa_id"])
        gravadas += 1
    _registrar(s, emp_id, "recebido", origem, arquivo, len(linhas), gravadas,
               avisos[:50])
    s.commit()
    return {"lidas": len(linhas), "gravadas": gravadas, "avisos": avisos[:50]}


def gravar_movimentos(s: Session, emp_id: int, linhas: list[dict],
                      origem="upload", arquivo=None) -> dict:
    """
    Grava o incorrido. Contas novas são criadas automaticamente em
    'A CLASSIFICAR' — nenhum lançamento é descartado por falta de cadastro,
    mas fica visível o que precisa ser classificado.
    """
    contas = {l["codigo"]: l["id"] for l in q(s, "SELECT id, codigo FROM conta")}
    novas = []
    gravadas = 0

    for m in linhas:
        codigo = m["conta_codigo"] or " - "
        if codigo not in contas:
            cid = q(s, """
                INSERT INTO conta (codigo, descricao, grupo, linha_dre, sinal)
                VALUES (:c, :d, 'GASTO', 'A CLASSIFICAR', -1)
                ON CONFLICT (codigo) DO UPDATE SET codigo = EXCLUDED.codigo
                RETURNING id
            """, c=codigo, d=codigo.split(" - ", 1)[-1] or codigo)[0]["id"]
            contas[codigo] = cid
            novas.append(codigo)

        q(s, """
            INSERT INTO movimento_realizado (empreendimento_id, sienge_bank_movement_id,
                sequencia, conta_id, data_movimento, valor, rateio_categoria,
                rateio_departamento, fornecedor, centro_custo, conciliado)
            VALUES (:e, :bid, :seq, :c, :d, :v, :rc, :rd, :f, :cc, :con)
            ON CONFLICT (sienge_bank_movement_id, conta_id, sequencia)
                 WHERE sienge_bank_movement_id IS NOT NULL DO UPDATE
               SET valor      = EXCLUDED.valor,
                   conciliado = EXCLUDED.conciliado
        """, e=emp_id, bid=m["sienge_bank_movement_id"],
            seq=m.get("sequencia", 1), c=contas[codigo],
            d=m["data_movimento"], v=m["valor"], rc=m["rateio_categoria"],
            rd=m["rateio_departamento"], f=m["fornecedor"],
            cc=m["centro_custo"], con=m["conciliado"])
        gravadas += 1

    avisos = ([f"{len(novas)} conta(s) nova(s) em 'A CLASSIFICAR'"] + novas[:20]) if novas else []
    _registrar(s, emp_id, "fin_obra", origem, arquivo, len(linhas), gravadas, avisos)
    s.commit()
    return {"lidas": len(linhas), "gravadas": gravadas, "contas_novas": novas,
            "avisos": avisos}
