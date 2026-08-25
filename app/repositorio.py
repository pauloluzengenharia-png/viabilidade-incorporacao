"""
Ponte entre o banco e o motor.

O motor (app/motor) não sabe que existe banco: recebe dataclasses e devolve
dataclasses. Este módulo é o único lugar que conhece as duas coisas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from .db import q
from .motor.engine import fim_mes
from .motor.modelo import (AtividadeObra, Obra, PlanoVendaMes, Premissas,
                           SituacaoUnidade, TabelaVenda, TipoVenda, Unidade)


@dataclass
class EntradasCenario:
    """Tudo que o motor precisa para rodar um cenário."""
    empreendimento: dict
    cenario: dict
    premissas: Premissas
    tabela: TabelaVenda
    tabela_investidor: Optional[TabelaVenda]
    unidades: list[Unidade]
    obra: Obra
    plano: list[PlanoVendaMes]
    receb_vendidos: dict[date, float]
    prazos_por_tipo: dict


# ---------------------------------------------------------------------
def carregar_unidades(s: Session, emp_id: int) -> list[Unidade]:
    """
    Reconstrói a aba Comercial: uma linha por unidade, com o realizado e a
    carteira já agregados a partir de parcela_recebida / parcela_receber.
    """
    linhas = q(s, """
        SELECT u.id, u.nome, u.area_privativa, u.situacao, u.tipo_venda,
               c.valor_bruto, c.comissao, c.data_contrato,
               COALESCE(rec.recebido, 0)      AS recebido,
               COALESCE(cart.poupanca, 0)     AS poupanca,
               COALESCE(cart.pos_chaves, 0)   AS pos_chaves,
               COALESCE(pt.preco_bruto, 0)    AS preco_tabela
          FROM unidade u
          LEFT JOIN LATERAL (
                SELECT valor_bruto, comissao, data_contrato
                  FROM contrato WHERE unidade_id = u.id
                 ORDER BY data_contrato DESC NULLS LAST LIMIT 1) c ON true
          LEFT JOIN LATERAL (
                SELECT sum(valor_liquido) AS recebido
                  FROM parcela_recebida WHERE unidade_id = u.id) rec ON true
          LEFT JOIN LATERAL (
                SELECT sum(valor) FILTER (WHERE condicao = 1) AS poupanca,
                       sum(valor) FILTER (WHERE condicao <> 1) AS pos_chaves
                  FROM parcela_receber WHERE unidade_id = u.id) cart ON true
          LEFT JOIN LATERAL (
                SELECT preco_bruto FROM preco_unidade
                 WHERE unidade_id = u.id ORDER BY vigente_desde DESC LIMIT 1) pt ON true
         WHERE u.empreendimento_id = :e
           AND u.considerar_na_viabilidade
         ORDER BY u.nome
    """, e=emp_id)

    saida = []
    for l in linhas:
        # O preço de tabela manda quando existe: é ele que define o VGV bruto.
        # O contrato traz o líquido (é o que o Sienge guarda), e a comissão é a
        # diferença — que nem sempre é o percentual da tabela: na Kiev, a
        # unidade 2205 fechou com 5,29% em vez de 6%. Derivar sempre pelo
        # percentual apagaria essa negociação; derivar pela diferença preserva.
        preco_tabela = float(l["preco_tabela"] or 0)
        bruto_contrato = float(l["valor_bruto"] or 0)
        liquido_contrato = bruto_contrato - float(l["comissao"] or 0)

        if preco_tabela and liquido_contrato:
            bruto, comissao = preco_tabela, preco_tabela - liquido_contrato
        elif preco_tabela:
            bruto, comissao = preco_tabela, float(l["comissao"] or 0)
        else:
            bruto, comissao = bruto_contrato, float(l["comissao"] or 0)
        situacao = SituacaoUnidade(l["situacao"])
        if situacao == SituacaoUnidade.PERMUTA:
            comissao = 0.0
        saida.append(Unidade(
            unidade=l["nome"],
            area_privativa=float(l["area_privativa"] or 0),
            valor_bruto=bruto,
            comissao=comissao,
            valor_liquido=bruto - comissao,
            situacao=situacao,
            tipo_venda=TipoVenda(l["tipo_venda"]),
            data_contrato=l["data_contrato"],
            recebido=float(l["recebido"] or 0),
            a_receber_poupanca=float(l["poupanca"] or 0),
            a_receber_pos_chaves=float(l["pos_chaves"] or 0),
            permuta_valor=(bruto - comissao) if situacao == SituacaoUnidade.PERMUTA else 0.0,
        ))
    return saida


def carregar_recebiveis_vendidos(s: Session, emp_id: int) -> dict[date, float]:
    """Comercial linha 207: o que a carteira já vendida traz, mês a mês."""
    linhas = q(s, """
        SELECT p.mes_vencimento AS mes, sum(p.valor) AS valor
          FROM parcela_receber p
          JOIN unidade u ON u.id = p.unidade_id
         WHERE u.empreendimento_id = :e
           AND u.considerar_na_viabilidade
         GROUP BY 1 ORDER BY 1
    """, e=emp_id)
    return {l["mes"]: float(l["valor"]) for l in linhas}


def carregar_obra(s: Session, emp_id: int) -> Obra:
    emp = q(s, "SELECT * FROM empreendimento WHERE id = :e", e=emp_id)[0]
    orc = q(s, """SELECT * FROM orcamento_obra
                   WHERE empreendimento_id = :e AND vigente""", e=emp_id)
    if not orc:
        raise ValueError("nenhum orçamento de obra vigente para este empreendimento")
    orc = orc[0]

    itens = q(s, """
        SELECT i.id, i.codigo, i.descricao, i.peso, i.variacao_negociada
          FROM eap_item i WHERE i.orcamento_id = :o ORDER BY i.codigo
    """, o=orc["id"])
    curvas = q(s, """
        SELECT c.eap_item_id, c.mes, c.perc_fisico
          FROM cronograma_item c
          JOIN eap_item i ON i.id = c.eap_item_id
         WHERE i.orcamento_id = :o
    """, o=orc["id"])

    por_item: dict[int, dict[date, float]] = {}
    for c in curvas:
        por_item.setdefault(c["eap_item_id"], {})[c["mes"]] = float(c["perc_fisico"])

    custo = float(orc["custo_raso"])
    atividades = [
        AtividadeObra(
            item=i["codigo"], atividade=i["descricao"],
            valor=custo * float(i["peso"]) * (1 + float(i["variacao_negociada"])),
            curva=por_item.get(i["id"], {}))
        for i in itens
    ]
    return Obra(custo_raso=-abs(custo),
                area_privativa=float(emp["area_privativa"]),
                area_construida=float(emp["area_construida"]),
                atividades=atividades)


def carregar_tabela(s: Session, emp_id: int, nome: str) -> Optional[TabelaVenda]:
    r = q(s, """SELECT * FROM tabela_venda
                 WHERE empreendimento_id = :e AND nome = :n""", e=emp_id, n=nome)
    if not r:
        return None
    t = r[0]
    return TabelaVenda(
        comissao=float(t["perc_comissao"]), ato=float(t["perc_ato"]),
        mensais=float(t["perc_mensais"]), anuais=float(t["perc_anuais"]),
        semestrais=float(t["perc_semestrais"]), unica=float(t["perc_unica"]),
        chaves=float(t["perc_chaves"]), n_mensais=int(t["qtd_mensais"]))


def carregar_premissas(s: Session, cenario: dict, emp: dict) -> Premissas:
    linhas = q(s, "SELECT chave, valor FROM premissa WHERE cenario_id = :c",
               c=cenario["id"])
    v = {l["chave"]: float(l["valor"]) for l in linhas}

    p = Premissas(nome=cenario["nome"], mes_base=cenario["mes_base"])
    for chave in ("preco_m2_estoque", "ret", "distratos", "despesas_comerciais",
                  "terreno_registro_perc", "taxa_adm_obra", "taxa_viabilizacao",
                  "decoracao", "projetos_e_outros", "marketing_stand",
                  "marketing_propaganda", "outras_desp_adm_perc", "outras_entradas",
                  "tma_anual", "financiamento_limite", "financiamento_juros_aa",
                  "financiamento_gatilho_obra"):
        if chave in v:
            setattr(p, chave, v[chave])
    if "financiamento_prazo_amort" in v:
        p.financiamento_prazo_amort = int(v["financiamento_prazo_amort"])
    if "meses_pos_chaves" in v:
        p.meses_pos_chaves = int(v["meses_pos_chaves"])

    parcelas = q(s, """SELECT valor FROM premissa_terreno
                        WHERE cenario_id = :c ORDER BY ordem""", c=cenario["id"])
    if parcelas:
        p.terreno_parcelas = [float(x["valor"]) for x in parcelas]

    # correção monetária: os índices são do cenário, a série é global
    p.indice_ate_chaves = cenario.get("indice_ate_chaves")
    p.indice_apos_chaves = cenario.get("indice_apos_chaves")
    p.indice_projetado_aa = v.get("indice_projetado_aa", 0.0)
    p.corrigir_custo_obra = bool(v.get("corrigir_custo_obra", 1.0))
    if p.corrige_carteira:
        usados = [c for c in (p.indice_ate_chaves, p.indice_apos_chaves) if c]
        serie: dict[str, dict[date, float]] = {}
        for l in q(s, """SELECT indice_codigo, mes, variacao FROM indice_mensal
                          WHERE indice_codigo = ANY(:cods) ORDER BY mes""",
                   cods=usados):
            serie.setdefault(l["indice_codigo"], {})[l["mes"]] = float(l["variacao"])
        p.serie_indice = serie

    preco = q(s, """SELECT * FROM preco_cenario
                     WHERE cenario_id = :c""", c=cenario["id"])
    for pr in preco:
        if pr["tipo_venda"] == "Normal":
            p.usar_preco_tabela = bool(pr["usar_tabela"])
            if pr["preco_m2"] is not None:
                p.preco_m2_estoque = float(pr["preco_m2"])
        elif pr["tipo_venda"] == "Investidor" and pr["preco_unidade"] is not None:
            p.preco_investidor_unidade = float(pr["preco_unidade"])
    return p


def carregar_plano(s: Session, cenario_id: int) -> list[PlanoVendaMes]:
    linhas = q(s, """SELECT mes, tipo_venda, quantidade FROM plano_venda
                      WHERE cenario_id = :c ORDER BY mes""", c=cenario_id)
    return [PlanoVendaMes(l["mes"], int(l["quantidade"]), TipoVenda(l["tipo_venda"]))
            for l in linhas]


# ---------------------------------------------------------------------
def carregar_entradas(s: Session, cenario_id: int) -> EntradasCenario:
    cen = q(s, "SELECT * FROM cenario WHERE id = :c", c=cenario_id)
    if not cen:
        raise ValueError(f"cenário {cenario_id} não existe")
    cen = cen[0]
    emp = q(s, "SELECT * FROM empreendimento WHERE id = :e",
            e=cen["empreendimento_id"])[0]

    tabela = carregar_tabela(s, emp["id"], "Padrão")
    if tabela is None:
        raise ValueError("empreendimento sem tabela de venda 'Padrão'")
    tabela.valida()
    tab_inv = carregar_tabela(s, emp["id"], "Investidor")

    prazos = {}
    if tab_inv:
        prazos[TipoVenda.INVESTIDOR] = {"n_mensais": tab_inv.n_mensais}

    return EntradasCenario(
        empreendimento=emp, cenario=cen,
        premissas=carregar_premissas(s, cen, emp),
        tabela=tabela, tabela_investidor=tab_inv,
        unidades=carregar_unidades(s, emp["id"]),
        obra=carregar_obra(s, emp["id"]),
        plano=carregar_plano(s, cen["id"]),
        receb_vendidos=carregar_recebiveis_vendidos(s, emp["id"]),
        prazos_por_tipo=prazos,
    )


def realizado_por_linha(s: Session, emp_id: int,
                        ate: Optional[date] = None) -> dict[str, float]:
    """
    A coluna REALIZADO da aba VIABILIDADE, sem a fronteira posicional:
    soma dos movimentos do Sienge até a data de corte, agrupados na linha do DRE.
    """
    linhas = q(s, """
        SELECT c.linha_dre, sum(m.valor) AS valor
          FROM movimento_realizado m
          JOIN conta c ON c.id = m.conta_id
         WHERE m.empreendimento_id = :e
           AND (CAST(:ate AS date) IS NULL OR m.mes_competencia <= CAST(:ate AS date))
         GROUP BY 1
    """, e=emp_id, ate=ate)
    saida = {l["linha_dre"]: float(l["valor"]) for l in linhas}

    # A comissão de corretagem não passa pela conta corrente da SPE: está
    # embutida no preço e é paga pela imobiliária. O "realizado" dela é
    # competência — a venda aconteceu, a comissão é devida. É assim que a
    # planilha faz (VM AD13 = −SUMIFS sobre as unidades vendidas), e sem isso
    # a coluna mostra zero numa linha de R$ 1,6 M.
    com = q(s, """
        SELECT sum(pu.preco_bruto - (c.valor_bruto - c.comissao)) AS v
          FROM unidade u
          JOIN contrato c ON c.unidade_id = u.id
          JOIN LATERAL (SELECT preco_bruto FROM preco_unidade
                         WHERE unidade_id = u.id
                         ORDER BY vigente_desde DESC LIMIT 1) pu ON true
         WHERE u.empreendimento_id = :e AND u.considerar_na_viabilidade
           AND u.situacao = 'Vendida'
           AND (CAST(:ate AS date) IS NULL OR c.data_contrato <= CAST(:ate AS date))
    """, e=emp_id, ate=ate)
    if com and com[0]["v"]:
        saida["(-) Comissão s/ vendas"] = -float(com[0]["v"])
    return saida
