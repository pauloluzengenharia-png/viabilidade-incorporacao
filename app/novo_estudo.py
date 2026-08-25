"""
Criação de um estudo de viabilidade do zero, pela tela.

Até aqui, um empreendimento só entrava no sistema por dois caminhos: a carga de
uma planilha de incorrido ou a importação do Sienge. Os dois pressupõem que o
empreendimento já existe. Este módulo cobre o caso anterior a esses dois — o
terreno que ainda está sendo avaliado, o estudo que ainda vai à diretoria.

Duas simplificações deliberadas, porque a alternativa seria pedir na primeira
tela um dado que nessa fase ainda não existe:

**O estoque nasce homogêneo.** Você informa quantas unidades e a área privativa
total; o sistema cria N unidades de área média, todas ao preço de tabela do
cenário. Isso é suficiente para o VGV, o fluxo e os indicadores. Quando o
cadastro real chegar — do Sienge ou de uma planilha —, a importação substitui
as unidades sintéticas pelas de verdade.

**A curva de obra é gerada por um formato.** Ninguém tem o cronograma
físico-financeiro detalhado no dia da avaliação do terreno. As três curvas
abaixo cobrem o comportamento real de uma obra vertical; a curva detalhada
entra depois, pelo orçamento.

O que NÃO é simplificado: as travas continuam valendo. A tabela de venda tem de
somar 100% e a curva tem de somar 100%, aqui como em qualquer outro caminho.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .db import q

# ---------------------------------------------------------------- curvas
#: Formatos de curva física oferecidos na tela. Cada função recebe o número de
#: meses e devolve a fração executada em cada mês — somando exatamente 1.
FORMATOS_CURVA = {
    "linear": "Linear — mesmo avanço todo mês",
    "s_suave": "Curva S suave — começa e termina devagar",
    "s_acentuada": "Curva S acentuada — concentra o meio da obra",
}


def curva(formato: str, meses: int) -> list[float]:
    """Fração física de cada mês. A soma é forçada a 1 no último mês."""
    if meses < 1:
        raise ValueError("a obra precisa de pelo menos um mês")
    if formato == "linear":
        pesos = [1.0] * meses
    else:
        # logística: quanto maior o k, mais concentrada no miolo
        k = 6.0 if formato == "s_suave" else 10.0
        def logistica(x): return 1 / (1 + math.exp(-k * (x - 0.5)))
        bordas = [logistica(i / meses) for i in range(meses + 1)]
        pesos = [bordas[i + 1] - bordas[i] for i in range(meses)]
    total = sum(pesos)
    fracoes = [p / total for p in pesos]
    # o resíduo do arredondamento vai para o último mês: a soma fecha 1 exato,
    # que é o que a trigger do banco confere
    fracoes[-1] += 1.0 - sum(fracoes)
    return fracoes


def fim_do_mes(d: dt.date) -> dt.date:
    prox = dt.date(d.year + (d.month == 12), (d.month % 12) + 1, 1)
    return prox - dt.timedelta(days=1)


def meses_entre(inicio: dt.date, fim: dt.date) -> int:
    return (fim.year - inicio.year) * 12 + (fim.month - inicio.month) + 1


def somar_meses(d: dt.date, n: int) -> dt.date:
    total = (d.year * 12 + d.month - 1) + n
    return fim_do_mes(dt.date(total // 12, total % 12 + 1, 1))


# ---------------------------------------------------------------- entrada
@dataclass
class Estudo:
    """O que a tela coleta. Um objeto só, para o erro aparecer todo de uma vez."""
    # empreendimento
    nome: str = ""
    sienge_enterprise_id: int | None = None
    area_privativa: float = 0.0
    area_construida: float = 0.0
    data_lancamento: dt.date | None = None
    data_entrega_prevista: dt.date | None = None

    # estoque
    unidades: int = 0
    preco_m2: float = 0.0

    # tabela de venda (frações do bruto)
    comissao: float = 0.06
    ato: float = 0.04
    mensais: float = 0.35
    anuais: float = 0.0
    semestrais: float = 0.35
    unica: float = 0.0
    chaves: float = 0.20
    n_mensais: int = 60

    # plano de vendas
    inicio_vendas: dt.date | None = None
    unidades_por_mes: float = 4.0

    # obra
    custo_raso: float = 0.0
    inicio_obra: dt.date | None = None
    meses_obra: int = 36
    formato_curva: str = "s_suave"

    # taxas e despesas
    taxa_adm_obra: float = 0.10
    taxa_viabilizacao: float = 0.05
    outras_desp_adm_perc: float = 0.015
    ret: float = 0.045
    distratos: float = 0.0
    despesas_comerciais: float = 0.0
    decoracao: float = 0.0
    projetos_e_outros: float = 0.0
    marketing_stand: float = 0.0
    marketing_propaganda: float = 0.0
    outras_entradas: float = 0.0

    # terreno
    terreno_parcelas: list = field(default_factory=list)   # [(valor, vencimento)]
    terreno_registro_perc: float = 0.025

    # correção e indicadores
    indice_ate_chaves: str = ""
    indice_apos_chaves: str = ""
    indice_projetado_aa: float = 0.0
    corrigir_custo_obra: bool = True
    tma_anual: float = 0.18
    meses_pos_chaves: int = 6

    # ------------------------------------------------------------ validação
    def erros(self) -> list[str]:
        """Tudo que impede o estudo de existir. Lista, não exceção: a tela
        mostra os problemas todos de uma vez em vez de um por recarregamento."""
        e: list[str] = []
        if not self.nome.strip():
            e.append("O empreendimento precisa de um nome.")
        if self.area_privativa <= 0:
            e.append("A área privativa precisa ser maior que zero — é a base do "
                     "preço por m² e do custo por m².")
        if self.area_construida <= 0:
            e.append("A área construída precisa ser maior que zero.")
        if self.area_construida < self.area_privativa:
            e.append("A área construída ficou menor que a privativa. A construída "
                     "inclui a comum e a garagem, então ela é sempre a maior.")
        if self.unidades < 1:
            e.append("Informe quantas unidades o empreendimento tem — sem "
                     "unidades não existe VGV.")
        if self.preco_m2 <= 0:
            e.append("O preço do estoque precisa ser maior que zero.")
        if self.custo_raso <= 0:
            e.append("O custo raso da obra precisa ser maior que zero.")

        soma = (self.comissao + self.ato + self.mensais + self.anuais
                + self.semestrais + self.unica + self.chaves)
        if abs(soma - 1) > 1e-6:
            e.append(f"A tabela de venda soma {soma*100:.2f}% e precisa somar "
                     f"exatamente 100%. Uma tabela que não fecha inventa um "
                     f"desconto — ou uma receita — que ninguém aprovou.")

        if not self.data_lancamento:
            e.append("Informe a data de lançamento.")
        if not self.data_entrega_prevista:
            e.append("Informe a entrega prevista — é ela que define o mês das chaves.")
        if (self.data_lancamento and self.data_entrega_prevista
                and self.data_entrega_prevista <= self.data_lancamento):
            e.append("A entrega prevista precisa ser depois do lançamento.")
        if self.meses_obra < 1:
            e.append("A obra precisa durar pelo menos um mês.")
        if self.formato_curva not in FORMATOS_CURVA:
            e.append("Escolha um formato de curva de obra.")
        if self.unidades_por_mes <= 0:
            e.append("A velocidade de vendas precisa ser maior que zero, senão o "
                     "estoque nunca é vendido e o fluxo não fecha.")
        if self.tma_anual <= 0:
            e.append("A TMA precisa ser maior que zero — é a taxa contra a qual o "
                     "projeto é comparado.")
        return e


# ---------------------------------------------------------------- criação
PREMISSAS_SIMPLES = [
    ("ret", "percentual"), ("distratos", "percentual"),
    ("despesas_comerciais", "moeda"), ("terreno_registro_perc", "percentual"),
    ("taxa_adm_obra", "percentual"), ("taxa_viabilizacao", "percentual"),
    ("decoracao", "moeda"), ("projetos_e_outros", "moeda"),
    ("marketing_stand", "moeda"), ("marketing_propaganda", "moeda"),
    ("outras_desp_adm_perc", "percentual"), ("outras_entradas", "moeda"),
    ("tma_anual", "percentual"), ("meses_pos_chaves", "meses"),
    ("indice_projetado_aa", "percentual"),
]


def criar(s: Session, d: Estudo) -> int:
    """
    Grava o estudo inteiro e devolve o id do empreendimento.

    Tudo numa transação só: ou o empreendimento nasce completo — cadastro,
    unidades, tabela, cenário, premissas, terreno, obra e plano de vendas — ou
    não nasce. Um cadastro pela metade é pior que nenhum, porque parece pronto.
    """
    erros = d.erros()
    if erros:
        raise ValueError(erros)

    emp = q(s, """
        INSERT INTO empreendimento
            (sienge_enterprise_id, nome, area_construida, area_privativa,
             data_lancamento, data_entrega_prevista, mes_corte_realizado)
        VALUES (:sid, :nome, :ac, :ap, :dl, :de, :corte)
        RETURNING id""",
        sid=d.sienge_enterprise_id, nome=d.nome.strip(),
        ac=d.area_construida, ap=d.area_privativa,
        dl=d.data_lancamento, de=d.data_entrega_prevista,
        corte=fim_do_mes(d.data_lancamento))[0]["id"]

    # --- estoque homogêneo -------------------------------------------------
    area_media = round(d.area_privativa / d.unidades, 2)
    resto = round(d.area_privativa - area_media * d.unidades, 2)
    for i in range(1, d.unidades + 1):
        area = area_media + (resto if i == d.unidades else 0)
        uid = q(s, """
            INSERT INTO unidade (empreendimento_id, nome, area_privativa,
                                 situacao, tipo_venda, origem_cadastro)
            VALUES (:e, :n, :a, 'Disponível', 'Normal', 'manual')
            RETURNING id""",
            e=emp, n=f"Unidade {i:03d}", a=area)[0]["id"]
        q(s, """INSERT INTO preco_unidade (unidade_id, preco_bruto, observacao)
                VALUES (:u, :p, 'preço de tabela do estudo inicial')""",
          u=uid, p=round(area * d.preco_m2, 2))

    # --- tabela de venda ---------------------------------------------------
    q(s, """
        INSERT INTO tabela_venda (empreendimento_id, nome, perc_comissao, perc_ato,
            perc_mensais, perc_anuais, perc_semestrais, perc_unica, perc_chaves,
            qtd_mensais)
        VALUES (:e,'Padrão',:c,:a,:m,:an,:se,:u,:ch,:qm)""",
      e=emp, c=d.comissao, a=d.ato, m=d.mensais, an=d.anuais,
      se=d.semestrais, u=d.unica, ch=d.chaves, qm=d.n_mensais)

    # --- cenário principal -------------------------------------------------
    mes_base = fim_do_mes(d.data_lancamento)
    horizonte = max(90, meses_entre(mes_base, d.data_entrega_prevista)
                    + d.meses_pos_chaves + 12)
    cen = q(s, """
        INSERT INTO cenario (empreendimento_id, nome, tipo, mes_base,
                             horizonte_meses, principal,
                             indice_ate_chaves, indice_apos_chaves)
        VALUES (:e,'realista','projecao',:mb,:h,true,:i1,:i2)
        RETURNING id""",
        e=emp, mb=mes_base, h=horizonte,
        i1=d.indice_ate_chaves or None, i2=d.indice_apos_chaves or None)[0]["id"]

    q(s, """INSERT INTO preco_cenario (cenario_id, tipo_venda, preco_m2, usar_tabela)
            VALUES (:c,'Normal',:p,true)""", c=cen, p=d.preco_m2)

    for chave, unidade in PREMISSAS_SIMPLES:
        q(s, """INSERT INTO premissa (cenario_id, chave, valor, unidade, origem)
                VALUES (:c,:k,:v,:u,'cadastro pela tela')""",
          c=cen, k=chave, v=float(getattr(d, chave)), u=unidade)
    q(s, """INSERT INTO premissa (cenario_id, chave, valor, unidade, origem)
            VALUES (:c,'corrigir_custo_obra',:v,'percentual','cadastro pela tela')""",
      c=cen, v=1 if d.corrigir_custo_obra else 0)

    # --- terreno -----------------------------------------------------------
    for ordem, (valor, venc) in enumerate(d.terreno_parcelas, start=1):
        q(s, """INSERT INTO premissa_terreno (cenario_id, ordem, valor, vencimento)
                VALUES (:c,:o,:v,:d)""", c=cen, o=ordem, v=valor, d=venc)

    # --- obra --------------------------------------------------------------
    inicio_obra = d.inicio_obra or d.data_lancamento
    orc = q(s, """
        INSERT INTO orcamento_obra (empreendimento_id, versao, custo_raso,
                                    data_base, indice_reajuste, vigente)
        VALUES (:e,'estudo inicial',:cr,:db,:ix,true) RETURNING id""",
        e=emp, cr=d.custo_raso, db=fim_do_mes(inicio_obra),
        ix=d.indice_ate_chaves or None)[0]["id"]
    item = q(s, """
        INSERT INTO eap_item (orcamento_id, codigo, descricao, peso)
        VALUES (:o,'01','OBRA — total, sem EAP detalhada',1.0) RETURNING id""",
        o=orc)[0]["id"]
    for i, fracao in enumerate(curva(d.formato_curva, d.meses_obra)):
        q(s, """INSERT INTO cronograma_item (eap_item_id, mes, perc_fisico)
                VALUES (:i,:m,:p)""",
          i=item, m=somar_meses(inicio_obra, i), p=fracao)

    # --- plano de vendas ---------------------------------------------------
    inicio = d.inicio_vendas or d.data_lancamento
    restam, mes, passo = d.unidades, 0, d.unidades_por_mes
    acumulado = 0.0
    while restam > 0 and mes < horizonte:
        acumulado += passo
        vender = min(restam, int(acumulado))
        acumulado -= vender
        if vender:
            q(s, """INSERT INTO plano_venda (cenario_id, mes, tipo_venda, quantidade)
                    VALUES (:c,:m,'Normal',:q)
                    ON CONFLICT (cenario_id, mes, tipo_venda)
                    DO UPDATE SET quantidade = plano_venda.quantidade + :q""",
              c=cen, m=somar_meses(inicio, mes), q=vender)
            restam -= vender
        mes += 1

    s.commit()
    return emp
