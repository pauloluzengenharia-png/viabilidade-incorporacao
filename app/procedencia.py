"""
De onde vem cada número da tabela de viabilidade.

Uma linha do resultado é o encontro de quatro coisas diferentes, e a pergunta
"de onde vem esse número" quase sempre é uma destas quatro:

    fórmula      como o motor chegou nele, com os operandos concretos
    insumos      qual premissa entrou, quanto vale, e quem a colocou ali
    lançamentos  quais movimentos do Sienge somam a coluna Realizado
    projeção     como o valor se distribui mês a mês no fluxo

Este módulo responde as quatro. Uma decisão de fundo: a fórmula é montada a
partir do **mesmo cálculo** que produziu o número — recebe o DRE, o bloco de VGV
e as premissas já calculados e descreve o que eles contêm. Escrever a fórmula
como texto solto seria criar uma segunda versão da conta, livre para divergir da
primeira sem ninguém notar. É exatamente o que a planilha fazia com as células
de memória.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from sqlalchemy.orm import Session

from .db import q


@dataclass
class Termo:
    """Um operando da fórmula, com o que ele é e de onde saiu."""
    rotulo: str
    valor: float
    origem: str = ""
    chave: str = ""          # premissa correspondente, quando há
    formato: str = "moeda"   # moeda | percentual | numero


@dataclass
class Formula:
    texto: str               # a conta em português
    termos: list = field(default_factory=list)
    resultado: float = 0.0
    nota: str = ""           # a ressalva que a linha costuma esconder
    #: Como a conta se escreve no Excel, com os operandos referidos por posição:
    #: `{1}`, `{2}` … e `{todos}` para a faixa inteira. É o que permite a
    #: planilha exportada recalcular quando alguém mexe num operando, em vez de
    #: trazer o resultado colado — que é o que a planilha antiga fazia.
    excel: str = "=SUM({todos})"


# =====================================================================
# a fórmula de cada linha, a partir do cálculo já feito
# =====================================================================
def formula_da_linha(rotulo: str, dre, bloco, obra, p,
                     composicao: Optional[list] = None) -> Optional[Formula]:
    """
    Recebe o resultado do motor e descreve a linha pedida.

    Nada é recalculado aqui: os valores vêm do mesmo objeto que alimentou a
    tabela. Se um dia a conta mudar no motor, esta tela muda junto.

    Quando a linha é um setor de custo, a fórmula é a própria composição: cada
    item vira um operando, e o resultado é a soma. Não há número solto para
    explicar — o número É a lista.
    """
    def t(rot, val, origem="", chave="", formato="moeda"):
        return Termo(rot, val, origem, chave, formato)

    if composicao is not None:
        return _formula_de_composicao(rotulo, composicao)

    receita_spe = bloco.receita_spe
    terreno_pgto = -sum(p.terreno_parcelas)
    custo_raso = -abs(obra.custo_raso)

    if rotulo == "VGV":
        return Formula(
            "Receita c/ vendas SPE − Comissão s/ vendas",
            [t("Receita c/ vendas SPE", receita_spe, "linha abaixo"),
             t("Comissão s/ vendas", bloco.deducoes_vgv, "linha abaixo")],
            dre.vgv,
            "É valor bruto, com a comissão dentro. A SPE nunca recebe o VGV.",
            "={1}-{2}")

    if rotulo == "(-) Comissão s/ vendas":
        return Formula(
            "Comissão das unidades já vendidas + comissão estimada do estoque",
            [t("Comissão das vendidas", -bloco.comissao_vendida,
               "diferença entre o preço de tabela e o líquido do contrato, "
               "unidade a unidade"),
             t("Comissão do estoque", -bloco.comissao_estoque,
               "percentual da tabela de venda sobre o estoque", "comissao")],
            dre.comissao,
            "Quando o preço bruto de uma unidade foi digitado à mão e não bate "
            "com o percentual, vale o preço digitado — a comissão daquela "
            "unidade é recalculada por diferença.")

    if rotulo == "RECEITA C/ VENDAS SPE":
        return Formula(
            "Vendidas + Permuta + Estoque + Já recebido do estoque − Comissão do estoque",
            [t("Unidades vendidas", bloco.vendida, "contratos do Sienge"),
             t("Permuta", bloco.permuta, "unidades marcadas como permuta"),
             t("Estoque", bloco.disponivel, "unidades disponíveis, ao preço do cenário"),
             t("Já recebido do estoque", bloco.recebido_disponivel,
               "recebimentos de unidades ainda marcadas como disponíveis"),
             t("Comissão do estoque", -bloco.comissao_estoque, "", "comissao")],
            dre.receita_spe,
            "É a base sobre a qual o RET e a taxa de carteira incidem.")

    if rotulo == "(-) Impostos":
        return Formula(
            "RET × Receita c/ vendas SPE",
            [t("Receita c/ vendas SPE", receita_spe, "linha acima"),
             t("RET", p.ret, "premissa do cenário", "ret", "percentual")],
            dre.ret,
            "Incide sobre o que a SPE recebe, não sobre o que ela fatura.",
            "=-{1}*{2}")

    if rotulo == "(-) Distratos":
        return Formula(
            "Distratos × VGV",
            [t("VGV", bloco.vgv, "linha acima"),
             t("Distratos", p.distratos, "premissa do cenário", "distratos", "percentual")],
            dre.distratos, "", "=-{1}*{2}")

    if rotulo == "(-) Despesas comerciais":
        return Formula(
            "Valor fixo da premissa",
            [t("Despesas comerciais", -p.despesas_comerciais,
               "premissa do cenário", "despesas_comerciais")],
            dre.despesas_comerciais, "", "={1}")

    if rotulo == "RECEITA LÍQUIDA":
        return Formula(
            "Receita c/ vendas SPE − Impostos − Distratos − Despesas comerciais",
            [t("Receita c/ vendas SPE", receita_spe),
             t("Impostos", dre.ret), t("Distratos", dre.distratos),
             t("Despesas comerciais", dre.despesas_comerciais)],
            dre.receita_liquida,
            "É a base de 100% da coluna “% da receita”.")

    if rotulo == "(-) Terreno - Permuta":
        return Formula(
            "Unidades entregues ao terreneiro, a preço de tabela",
            [t("Permuta", -bloco.permuta, "unidades com situação Permuta"),
             t("Disponível Leal", -bloco.disponivel_leal,
               "unidades reservadas ao terreneiro ainda não transferidas")],
            dre.terreno_permuta,
            "Entra no resultado e não entra no fluxo de caixa: paga-se em "
            "unidade, não em dinheiro. É uma das razões de somar as colunas "
            "mensais nunca dar a linha do resultado.")

    if rotulo == "(-) Terreno - Pagamento":
        return Formula(
            f"Soma das {len(p.terreno_parcelas)} parcelas em dinheiro",
            [t(f"Parcela {i}", -v, "cadastro do terreno")
             for i, v in enumerate(p.terreno_parcelas, start=1)],
            dre.terreno_pagamento)

    if rotulo == "(-) Terreno - Outros":
        return Formula(
            "Registro e ITBI × Pagamento do terreno",
            [t("Pagamento do terreno", terreno_pgto, "linha acima"),
             t("Registro e ITBI", p.terreno_registro_perc, "premissa do cenário",
               "terreno_registro_perc", "percentual")],
            dre.terreno_registro, "", "={1}*{2}")

    if rotulo == "(-) Obra - Custo Raso":
        return Formula(
            "Custo raso do orçamento vigente",
            [t("Custo raso", custo_raso, "orçamento de obra vigente")],
            dre.obra_custo_raso,
            "A curva física distribui esse valor no tempo — ela não o altera.",
            "={1}")

    if rotulo == "(-) Taxa de Administração - Obra":
        return Formula(
            "Taxa de administração × Custo raso",
            [t("Custo raso", custo_raso, "linha acima"),
             t("Taxa de administração — obra", p.taxa_adm_obra,
               "premissa do cenário", "taxa_adm_obra", "percentual")],
            dre.taxa_adm_obra, "", "={1}*{2}")

    if rotulo == "(-) Taxa de Administração - Carteira":
        return Formula(
            "Taxa de carteira × Receita c/ vendas SPE",
            [t("Receita c/ vendas SPE", receita_spe),
             t("Taxa de administração — carteira", p.taxa_viabilizacao,
               "premissa do cenário", "taxa_viabilizacao", "percentual")],
            dre.taxa_viabilizacao, "", "=-{1}*{2}")

    if rotulo == "(-) Outras despesas administrativas":
        return Formula(
            "Outras despesas administrativas × Custo raso",
            [t("Custo raso", custo_raso),
             t("Outras despesas administrativas", p.outras_desp_adm_perc,
               "premissa do cenário", "outras_desp_adm_perc", "percentual")],
            dre.outras_desp_adm,
            "A planilha usava um percentual de sete casas, resultado de uma "
            "divisão feita uma vez. Aqui o percentual é redondo — a diferença "
            "é de arredondamento e não vale correção.",
            "={1}*{2}")

    fixas = {
        "(-) Incorporação - Decoração": ("decoracao", dre.decoracao),
        "(-) Incorporação - Outros": ("projetos_e_outros", dre.projetos_e_outros),
        "(-) Marketing - Stand": ("marketing_stand", dre.marketing_stand),
        "(-) Marketing - Propaganda": ("marketing_propaganda", dre.marketing_propaganda),
        "(+) Outras receitas administrativas": ("outras_entradas", dre.outras_entradas),
    }
    if rotulo in fixas:
        chave, valor = fixas[rotulo]
        return Formula("Valor fixo da premissa",
                       [t(rotulo.split(" - ")[-1].lstrip("(-+) "), valor,
                          "premissa do cenário", chave)],
                       valor, "", "={1}")

    if rotulo == "LUCRO":
        return Formula(
            "Receita líquida − terreno − obra − taxas − incorporação − marketing",
            [t("Receita líquida", dre.receita_liquida),
             t("Terreno — permuta", dre.terreno_permuta),
             t("Terreno — pagamento", dre.terreno_pagamento),
             t("Terreno — outros", dre.terreno_registro),
             t("Obra — custo raso", dre.obra_custo_raso),
             t("Taxa de adm. — obra", dre.taxa_adm_obra),
             t("Taxa de adm. — carteira", dre.taxa_viabilizacao),
             t("Decoração", dre.decoracao),
             t("Incorporação — outros", dre.projetos_e_outros),
             t("Marketing — stand", dre.marketing_stand),
             t("Marketing — propaganda", dre.marketing_propaganda),
             t("Outras despesas administrativas", dre.outras_desp_adm),
             t("Outras receitas administrativas", dre.outras_entradas)],
            dre.lucro if hasattr(dre, "lucro") else 0.0,
            "É lucro do empreendimento inteiro, do lançamento à última parcela. "
            "Não é lucro anual e não desconta imposto de renda do sócio.")

    return None


def _formula_de_composicao(rotulo: str, itens: list) -> Formula:
    """A linha é a soma dos itens que alguém orçou, um a um."""
    termos = []
    for i in itens:
        detalhe = []
        if i.get("quantidade") and i.get("valor_unitario"):
            unidade = f" {i['unidade']}" if i.get("unidade") else ""
            detalhe.append(f"{float(i['quantidade']):g}{unidade} × "
                           f"R$ {float(i['valor_unitario']):,.2f}".replace(",", " "))
        if i.get("observacao"):
            detalhe.append(i["observacao"])
        termos.append(Termo(i["descricao"], -float(i["valor"]),
                            " · ".join(detalhe) or "item da composição"))
    if not termos:
        return Formula(
            "Nenhum item na composição — a linha vale zero", [], 0.0,
            "Este setor ainda não foi orçado. Enquanto não tiver item nenhum, "
            "ele não entra no resultado — o que é diferente de custar zero.")
    quantos = ("Soma do único item da composição" if len(termos) == 1
               else f"Soma dos {len(termos)} itens da composição")
    return Formula(quantos, termos,
                   sum(x.valor for x in termos),
                   "Cada item é orçado na tela de custos deste setor. Mudar "
                   "qualquer um deles muda a linha do resultado.")


def composicao_da_linha(s: Session, cenario_id: int, rotulo: str):
    """
    Os itens que compõem a linha, quando ela é um setor de custo.

    Devolve `None` para as linhas que não são setor — e a diferença importa:
    `None` significa "esta linha tem fórmula própria", lista vazia significa
    "é setor de custo e ninguém orçou ainda".
    """
    setor = q(s, "SELECT codigo, nome, resumo FROM setor_custo WHERE linha_dre = :l",
              l=rotulo)
    if not setor:
        return None, None
    itens = q(s, """SELECT * FROM composicao_item
                     WHERE cenario_id = :c AND setor = :s
                     ORDER BY ordem, id""", c=cenario_id, s=setor[0]["codigo"])
    return setor[0], itens


# =====================================================================
# de onde veio cada premissa: carga, importação ou alguém editando
# =====================================================================
def origem_das_premissas(s: Session, cenario_id: int,
                         chaves: list[str]) -> dict[str, dict]:
    """
    Para cada premissa, o que está gravado e a última vez que alguém mexeu.

    `origem` é o campo que a própria linha da premissa carrega ('migração da
    planilha', 'editado na tela'); a alteração vem do registro de alterações.
    """
    if not chaves:
        return {}
    linhas = q(s, """SELECT chave, valor, unidade, origem FROM premissa
                      WHERE cenario_id = :c AND chave = ANY(:ks)""",
               c=cenario_id, ks=list(chaves))
    saida = {l["chave"]: {"valor": float(l["valor"]), "unidade": l["unidade"],
                          "origem": l["origem"], "alteracao": None}
             for l in linhas}

    mexidas = q(s, """
        SELECT DISTINCT ON (campo) campo, valor_anterior, valor_novo, autor, em
          FROM alteracao
         WHERE cenario_id = :c AND campo = ANY(:ks)
         ORDER BY campo, em DESC""", c=cenario_id, ks=list(chaves))
    for m in mexidas:
        if m["campo"] in saida:
            saida[m["campo"]]["alteracao"] = m
    return saida


# =====================================================================
# os lançamentos que somam a coluna Realizado
# =====================================================================
def lancamentos_da_linha(s: Session, emp_id: int, linha: str,
                         ate: Optional[date] = None) -> dict:
    """
    Os movimentos do Sienge classificados naquela linha, com o total.

    Duas linhas não vêm de movimento nenhum e são explicadas em vez de
    listadas: a comissão é competência (a venda aconteceu, a comissão é
    devida) e os subtotais são soma de outras linhas.
    """
    if linha == "(-) Comissão s/ vendas":
        vendas = q(s, """
            SELECT u.nome, c.data_contrato, pu.preco_bruto,
                   (c.valor_bruto - c.comissao) AS liquido,
                   (pu.preco_bruto - (c.valor_bruto - c.comissao)) AS comissao
              FROM unidade u
              JOIN contrato c ON c.unidade_id = u.id
              JOIN LATERAL (SELECT preco_bruto FROM preco_unidade
                             WHERE unidade_id = u.id
                             ORDER BY vigente_desde DESC LIMIT 1) pu ON true
             WHERE u.empreendimento_id = :e AND u.considerar_na_viabilidade
               AND u.situacao = 'Vendida'
               AND (CAST(:ate AS date) IS NULL OR c.data_contrato <= CAST(:ate AS date))
             ORDER BY c.data_contrato, u.nome""", e=emp_id, ate=ate)
        return {"tipo": "comissao", "linhas": vendas,
                "total": -sum(float(v["comissao"]) for v in vendas),
                "nota": "A comissão não passa pela conta corrente da SPE: está "
                        "embutida no preço e é paga pela imobiliária. O realizado "
                        "dela é competência — a venda aconteceu, a comissão é "
                        "devida. Por isso a lista abaixo é de contratos, não de "
                        "pagamentos."}

    movimentos = q(s, """
        SELECT m.data_movimento, m.mes_competencia, m.valor, m.fornecedor,
               m.centro_custo, m.rateio_categoria, m.rateio_departamento,
               c.codigo AS conta_codigo, c.descricao AS conta,
               u.nome AS unidade
          FROM movimento_realizado m
          JOIN conta c ON c.id = m.conta_id
          LEFT JOIN unidade u ON u.id = m.unidade_id
         WHERE m.empreendimento_id = :e AND c.linha_dre = :l
           AND (CAST(:ate AS date) IS NULL OR m.mes_competencia <= CAST(:ate AS date))
         ORDER BY m.data_movimento, c.codigo""", e=emp_id, l=linha, ate=ate)

    por_conta = q(s, """
        SELECT c.codigo, c.descricao, count(*) AS n, sum(m.valor) AS valor
          FROM movimento_realizado m
          JOIN conta c ON c.id = m.conta_id
         WHERE m.empreendimento_id = :e AND c.linha_dre = :l
           AND (CAST(:ate AS date) IS NULL OR m.mes_competencia <= CAST(:ate AS date))
         GROUP BY c.codigo, c.descricao
         ORDER BY sum(m.valor)""", e=emp_id, l=linha, ate=ate)

    return {"tipo": "movimentos", "linhas": movimentos, "por_conta": por_conta,
            "total": sum(float(m["valor"]) for m in movimentos),
            "nota": ""}


def projecao_da_linha(s: Session, emp_id: int, cenario_id: int,
                      rotulo: str) -> list[dict]:
    """
    O valor mês a mês daquela linha, no fluxo já gravado.

    `fluxo_projetado.linha_dre` guarda o rótulo da linha do resultado, não o
    código interno do fluxo — a agregação já foi feita na gravação da rodada.
    Nem toda linha tem projeção: permuta é custo e não é caixa, e os subtotais
    são soma de outras. Nesses casos a lista volta vazia e a tela omite a seção.
    """
    return q(s, """
        SELECT fp.mes, sum(fp.valor) AS valor
          FROM fluxo_projetado fp
         WHERE fp.rodada_id = (SELECT r.id FROM rodada r
                                WHERE r.cenario_id = :c
                                ORDER BY r.executada_em DESC LIMIT 1)
           AND fp.linha_dre = :l
         GROUP BY fp.mes ORDER BY fp.mes""", c=cenario_id, l=rotulo)


def importacoes(s: Session, emp_id: int) -> list[dict]:
    """De qual arquivo vieram os lançamentos — o histórico da importação."""
    return q(s, """SELECT * FROM importacao WHERE empreendimento_id = :e
                    ORDER BY importado_em DESC LIMIT 12""", e=emp_id)
