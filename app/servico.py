"""
Serviço de cálculo: roda um cenário e persiste o resultado.

Uma rodada é imutável e carrega o hash das entradas. É isso que permite
responder "por que o número mudou desde a semana passada?" — pergunta que
a planilha nunca consegue responder.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from .db import q
from .motor.engine import (DRE, FluxoMensal, calcular_dre, calcular_indicadores,
                           calcular_vgv, fim_mes, montar_fluxo)
from .motor.modelo import SituacaoUnidade, TipoVenda
from .repositorio import EntradasCenario, carregar_entradas, realizado_por_linha


def _hash(e: EntradasCenario) -> str:
    def serial(o):
        if isinstance(o, date):
            return o.isoformat()
        if hasattr(o, "value"):
            return o.value
        return str(o)

    # a série de índices sai daqui porque tem data como CHAVE de dicionário, e
    # json.dumps não aceita isso; ela entra logo abaixo, já serializada
    premissas = {k: v for k, v in asdict(e.premissas).items() if k != "serie_indice"}

    bruto = json.dumps({
        "premissas": premissas,
        "tabela": asdict(e.tabela),
        "unidades": [asdict(u) for u in e.unidades],
        "obra": {"custo": e.obra.custo_raso,
                 "atividades": [{"item": a.item, "valor": a.valor,
                                 "curva": {m.isoformat(): v
                                           for m, v in sorted(a.curva.items())}}
                                for a in e.obra.atividades]},
        "plano": [asdict(p) for p in e.plano],
        "receb": {k.isoformat(): v for k, v in sorted(e.receb_vendidos.items())},
        "correcao": {
            "ate_chaves": e.premissas.indice_ate_chaves,
            "apos_chaves": e.premissas.indice_apos_chaves,
            "projetado_aa": e.premissas.indice_projetado_aa,
            "serie": {cod: {m.isoformat(): val for m, val in sorted(s_.items())}
                      for cod, s_ in sorted(e.premissas.serie_indice.items())},
        },
    }, default=serial, sort_keys=True)
    return hashlib.sha256(bruto.encode()).hexdigest()[:32]


def valor_unidade_padrao(e: EntradasCenario) -> float:
    """
    SIMULAÇÕES M70/M78: preço médio da unidade de estoque, líquido de comissão.
    É a base de cada coorte do motor de recebíveis.
    """
    disp = [u for u in e.unidades
            if u.situacao == SituacaoUnidade.DISPONIVEL
            and u.tipo_venda == TipoVenda.NORMAL]
    if not disp:
        return 0.0
    p = e.premissas
    bruto = sum((u.valor_bruto if p.usar_preco_tabela
                 else u.area_privativa * p.preco_m2_estoque) for u in disp)
    return bruto / len(disp) * (1 - e.tabela.comissao)


def calcular(e: EntradasCenario) -> tuple[DRE, FluxoMensal, object]:
    """Roda o motor. Função pura — não toca no banco."""
    bloco = calcular_vgv(e.unidades, e.premissas, e.tabela)
    dre = calcular_dre(bloco, e.obra, e.premissas)

    entrega = e.empreendimento.get("data_entrega_prevista")
    fluxo = montar_fluxo(
        e.unidades, e.obra, e.premissas, e.tabela, e.plano,
        horizonte=int(e.cenario["horizonte_meses"]),
        receb_vendidos=e.receb_vendidos,
        valor_unidade_padrao=valor_unidade_padrao(e),
        mes_chaves=fim_mes(entrega) if entrega else None,
        prazos_por_tipo=e.prazos_por_tipo,
    )
    area = sum(u.area_privativa for u in e.unidades)
    ind = calcular_indicadores(dre, e.obra, fluxo, e.premissas, area)
    return dre, fluxo, ind


# ---------------------------------------------------------------------
LINHAS_DRE = [
    ("VGV", "vgv"),
    ("(-) Comissão s/ vendas", "comissao"),
    ("RECEITA C/ VENDAS SPE", "receita_spe"),
    ("(-) Impostos", "ret"),
    ("(-) Distratos", "distratos"),
    ("(-) Despesas comerciais", "despesas_comerciais"),
    ("RECEITA LÍQUIDA", "receita_liquida"),
    ("(-) Terreno - Permuta", "terreno_permuta"),
    ("(-) Terreno - Pagamento", "terreno_pagamento"),
    ("(-) Terreno - Outros", "terreno_registro"),
    ("(-) Obra - Custo Raso", "obra_custo_raso"),
    ("(-) Taxa de Administração - Obra", "taxa_adm_obra"),
    ("(-) Taxa de Administração - Carteira", "taxa_viabilizacao"),
    ("(-) Incorporação - Decoração", "decoracao"),
    ("(-) Incorporação - Outros", "projetos_e_outros"),
    ("(-) Marketing - Stand", "marketing_stand"),
    ("(-) Marketing - Propaganda", "marketing_propaganda"),
    ("(-) Outras despesas administrativas", "outras_desp_adm"),
    ("(+) Outras receitas administrativas", "outras_entradas"),
    ("LUCRO", "lucro"),
]

# de que linha do fluxo mensal sai cada linha do DRE
LINHA_DE_CODIGO = {
    "1010101": "RECEITA C/ VENDAS SPE", "ESTOQUE": "RECEITA C/ VENDAS SPE",
    "POS_CHAVES_V": "RECEITA C/ VENDAS SPE", "POS_CHAVES_E": "RECEITA C/ VENDAS SPE",
    "IMPOSTOS": "(-) Impostos", "DESP_COM": "(-) Despesas comerciais",
    "OBRA": "(-) Obra - Custo Raso", "TX_OBRA": "(-) Taxa de Administração - Obra",
    "TX_CART": "(-) Taxa de Administração - Carteira",
    "TERRENO": "(-) Terreno - Pagamento", "TERR_REG": "(-) Terreno - Outros",
    "DECOR": "(-) Incorporação - Decoração", "PROJ": "(-) Incorporação - Outros",
    "STAND": "(-) Marketing - Stand", "PROP": "(-) Marketing - Propaganda",
    "OUTRAS_ADM": "(-) Outras despesas administrativas",
    "CORRECAO": "(+) Correção monetária da carteira",
    "CORRECAO_OBRA": "(-) Correção monetária da obra",
    "FIN_CAPT": "(+) Receitas c/ financiamento",
    "FIN_AMORT": "(-) Amortização de financiamento",
    "FIN_JUROS": "(-) Juros s/ financiamento",
}


def rodar_e_persistir(s: Session, cenario_id: int,
                      executada_por: str = "sistema",
                      forcar: bool = False) -> int:
    """Roda o cenário e grava a rodada. Devolve o id da rodada."""
    cen = q(s, "SELECT tipo, congelado_em FROM cenario WHERE id = :c", c=cenario_id)
    if cen and cen[0]["tipo"] == "orcado":
        # o orçado é um resultado aprovado, não uma projeção: recalcular
        # apagaria justamente o número contra o qual se compara
        ja = q(s, """SELECT id FROM rodada WHERE cenario_id = :c
                      ORDER BY executada_em DESC LIMIT 1""", c=cenario_id)
        if ja:
            return ja[0]["id"]
        raise ValueError("cenário orçado sem resultado congelado — informe os "
                         "valores aprovados antes de compará-los")
    e = carregar_entradas(s, cenario_id)
    h = _hash(e)

    if not forcar:
        ja = q(s, """SELECT id FROM rodada
                      WHERE cenario_id = :c AND hash_entradas = :h""",
               c=cenario_id, h=h)
        if ja:
            return ja[0]["id"]

    dre, fluxo, ind = calcular(e)

    rodada_id = q(s, """
        INSERT INTO rodada (cenario_id, executada_por, hash_entradas)
        VALUES (:c, :u, :h)
        ON CONFLICT (cenario_id, hash_entradas) DO UPDATE SET executada_em = now()
        RETURNING id
    """, c=cenario_id, u=executada_por, h=h)[0]["id"]

    q(s, "DELETE FROM fluxo_projetado WHERE rodada_id = :r", r=rodada_id)
    q(s, "DELETE FROM resultado_projetado WHERE rodada_id = :r", r=rodada_id)

    # --- fluxo mensal, agrupado nas linhas do DRE ---
    acumulado: dict[tuple[str, date], float] = {}
    for linha in fluxo.linhas:
        alvo = LINHA_DE_CODIGO.get(linha.codigo, linha.descricao)
        for mes, valor in linha.valores.items():
            if valor:
                acumulado[(alvo, mes)] = acumulado.get((alvo, mes), 0.0) + valor

    if acumulado:
        s.execute(_INSERT_FLUXO, [
            {"r": rodada_id, "l": l, "m": m, "v": round(v, 2)}
            for (l, m), v in acumulado.items()
        ])

    # --- o DRE, em tabela própria ---
    # NÃO é a soma do fluxo: o fluxo só vê o caixa dentro do horizonte, e o
    # resultado inclui a permuta (que não passa por caixa) e as parcelas que
    # caem depois do fim da projeção.
    for ordem, (rotulo, campo) in enumerate(LINHAS_DRE):
        q(s, """INSERT INTO resultado_projetado (rodada_id, linha_dre, valor, ordem)
                VALUES (:r, :l, :v, :o)
                ON CONFLICT (rodada_id, linha_dre) DO UPDATE
                   SET valor = EXCLUDED.valor, ordem = EXCLUDED.ordem""",
          r=rodada_id, l=rotulo, v=round(getattr(dre, campo, 0.0) or 0.0, 2), o=ordem)

    q(s, "DELETE FROM indicador WHERE rodada_id = :r", r=rodada_id)
    q(s, """
        INSERT INTO indicador (rodada_id, vgv, receita_liquida, lucro, margem,
            custo_m2_privativa, preco_m2_vgv, eficiencia, exposicao_maxima,
            mes_exposicao, vpl, tir_anual, mtir_anual, aporte_necessario)
        VALUES (:r, :vgv, :rl, :lucro, :margem, :cm2, :pm2, :ef, :exp, :mexp,
                :vpl, :tir, :mtir, :aporte)
    """, r=rodada_id, vgv=round(ind.vgv, 2), rl=round(ind.receita_liquida, 2),
        lucro=round(ind.lucro, 2), margem=round(ind.margem, 6),
        cm2=round(ind.custo_m2_privativa, 4), pm2=round(ind.preco_m2_vgv, 4),
        ef=round(ind.eficiencia, 6), exp=round(ind.exposicao_maxima, 2),
        mexp=ind.mes_exposicao, vpl=round(ind.vpl, 2),
        tir=round(ind.tir_anual, 6) if ind.tir_anual is not None else None,
        mtir=round(ind.mtir_anual, 6) if ind.mtir_anual is not None else None,
        aporte=round(ind.aporte_necessario, 2))

    s.commit()
    return rodada_id


from sqlalchemy import text as _t
_INSERT_FLUXO = _t("""INSERT INTO viab.fluxo_projetado (rodada_id, linha_dre, mes, valor)
                      VALUES (:r, :l, :m, :v)
                      ON CONFLICT (rodada_id, linha_dre, mes)
                      DO UPDATE SET valor = EXCLUDED.valor""")


# ---------------------------------------------------------------------
# subtotais: no realizado eles não vêm de conta nenhuma (são somas), então
# são recompostos a partir das analíticas — senão a coluna mostraria zero
# justamente nas linhas que a diretoria lê primeiro.
COMPOSICAO_SUBTOTAL = {
    "VGV": ["RECEITA C/ VENDAS SPE", "(-) Comissão s/ vendas"],
    "RECEITA LÍQUIDA": ["RECEITA C/ VENDAS SPE", "(-) Impostos", "(-) Distratos",
                        "(-) Despesas comerciais"],
    "LUCRO": ["RECEITA LÍQUIDA", "(-) Terreno - Permuta", "(-) Terreno - Pagamento",
              "(-) Terreno - Outros", "(-) Obra - Custo Raso",
              "(-) Taxa de Administração - Obra", "(-) Taxa de Administração - Carteira",
              "(-) Incorporação - Decoração", "(-) Incorporação - Outros",
              "(-) Marketing - Stand", "(-) Marketing - Propaganda",
              "(-) Outras despesas administrativas",
              "(+) Outras receitas administrativas"],
}


def _fechar_subtotais(valores: dict[str, float]) -> None:
    """VGV = receita − comissão (a comissão é negativa, por isso subtrai)."""
    valores["VGV"] = (valores.get("RECEITA C/ VENDAS SPE", 0.0)
                      - valores.get("(-) Comissão s/ vendas", 0.0))
    for alvo in ("RECEITA LÍQUIDA", "LUCRO"):
        valores[alvo] = sum(valores.get(c, 0.0) for c in COMPOSICAO_SUBTOTAL[alvo])


def visao_viabilidade(s: Session, emp_id: int,
                      ate: Optional[date] = None) -> list[dict]:
    """
    A aba VIABILIDADE, funcionando: orçado × atualizado × realizado × a realizar.
    Sem #REF!, e com a data de corte explícita em vez de implícita na coluna.
    """
    emp = q(s, "SELECT * FROM empreendimento WHERE id = :e", e=emp_id)[0]
    ate = ate or emp.get("mes_corte_realizado")

    def total_por_linha(tipo: str) -> dict[str, float]:
        # o orçado vem do cenário congelado; o atualizado, do cenário principal
        filtro = "c2.tipo = 'orcado'" if tipo == "orcado" else "c2.principal"
        linhas = q(s, f"""
            SELECT rp.linha_dre, rp.valor
              FROM resultado_projetado rp
             WHERE rp.rodada_id = (SELECT r2.id FROM rodada r2
                                     JOIN cenario c2 ON c2.id = r2.cenario_id
                                    WHERE c2.empreendimento_id = :e AND {filtro}
                                    ORDER BY r2.executada_em DESC LIMIT 1)
        """, e=emp_id)
        return {l["linha_dre"]: float(l["valor"]) for l in linhas}

    orcado = total_por_linha("orcado")
    atualizado = total_por_linha("projecao")
    realizado = realizado_por_linha(s, emp_id, ate)
    _fechar_subtotais(realizado)

    base = atualizado.get("RECEITA LÍQUIDA") or 1.0
    saida = []
    for rotulo, _ in LINHAS_DRE:
        o, a, r = orcado.get(rotulo, 0.0), atualizado.get(rotulo, 0.0), realizado.get(rotulo, 0.0)
        saida.append({
            "linha": rotulo, "orcado": o, "atualizado": a, "realizado": r,
            "a_realizar": a - r,
            "variacao": (a / o - 1) if o else None,
            "perc_vv": a / base,
            "destaque": rotulo.isupper(),
        })
    return saida
